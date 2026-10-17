"""pisugar-server client over its Unix socket (SPEC.md §6.5), and a fake server.

Replies are `<name>: <value>` lines: `get battery` → `battery: 87.5`; setters answer
`<command>: done`. Button taps arrive as bare `single`, `double` or `long` lines on every
connection, mixed in with replies. A bad command gets a multi-line message with no prefix.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

TAPS = frozenset({"single", "double", "long"})
REPLY_TIMEOUT = 2.0
CONNECT_TIMEOUT = 10.0
IO_ERROR = "I/O error"
IO_RETRIES = 3
IO_RETRY_DELAY = 0.1


class PiSugarError(Exception):
    pass


class PiSugarClient:
    def __init__(self, sock_path: Path, on_tap: Callable[[str], None] | None = None, log=None):
        self.sock_path = str(sock_path)
        self.on_tap = on_tap or (lambda tap: None)
        self.log = log or (lambda *_: None)
        self._lock = asyncio.Lock()
        self._cmd: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        self._events_task: asyncio.Task | None = None
        self.connected = False

    # Connections ---------------------------------------------------------------
    async def connect(self, timeout: float = CONNECT_TIMEOUT) -> bool:
        """Connect both sockets, retrying while pisugar-server starts. False if it never appears."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        delay = 0.2
        while True:
            try:
                self._cmd = await asyncio.open_unix_connection(self.sock_path)
                break
            except OSError:
                if loop.time() >= deadline:
                    self.connected = False
                    return False
                await asyncio.sleep(delay)
                delay = min(delay * 2, 2.0)
        self.connected = True
        if self._events_task is None or self._events_task.done():
            self._events_task = asyncio.create_task(self._events())
        return True

    async def _events(self) -> None:
        """A second connection that only listens for taps; reconnects if it drops."""
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(self.sock_path)
            except OSError:
                await asyncio.sleep(2.0)
                continue
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    word = line.decode(errors="replace").strip()
                    if word in TAPS:
                        self.on_tap(word)
            except (ConnectionError, OSError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    writer.close()
            await asyncio.sleep(1.0)

    async def _close_cmd(self) -> None:
        if self._cmd:
            with contextlib.suppress(Exception):
                self._cmd[1].close()
        self._cmd = None

    async def close(self) -> None:
        if self._events_task:
            self._events_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._events_task
        await self._close_cmd()

    # Requests -------------------------------------------------------------------
    async def _request_once(self, line: str, expect: str) -> str:
        if self._cmd is None:
            self._cmd = await asyncio.wait_for(asyncio.open_unix_connection(self.sock_path), REPLY_TIMEOUT)
        reader, writer = self._cmd
        # Drop anything left over (taps, or a reply that came after a timeout).
        while True:
            try:
                stale = await asyncio.wait_for(reader.readline(), 0.001)
            except TimeoutError:
                break
            if not stale:
                raise ConnectionError("pisugar-server closed the connection")
        writer.write((line + "\n").encode())
        await writer.drain()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REPLY_TIMEOUT
        errors: list[str] = []
        prefix = expect + ":"
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                if errors:
                    raise PiSugarError(" ".join(errors))
                raise TimeoutError(f"no reply to {line!r}")
            try:
                raw = await asyncio.wait_for(reader.readline(), remaining if not errors else min(remaining, 0.2))
            except TimeoutError:
                if errors:
                    raise PiSugarError(" ".join(errors)) from None
                raise
            if not raw:
                raise ConnectionError("pisugar-server closed the connection")
            text = raw.decode(errors="replace").strip()
            if not text or text in TAPS:
                if text in TAPS:
                    self.on_tap(text)
                continue
            if text.startswith(prefix):
                return text[len(prefix):].strip()
            errors.append(text)

    async def request(self, line: str, expect: str) -> str:
        """Send one command and return its value.

        pisugar-server passes I2C errors on as the value ("I/O error: Remote I/O error (os
        error 121)"). The PiSugar 3 gives one now and then, for example straight after an alarm
        write (about 1 read in 20 on the bench, 2026-10-16), so those are retried."""
        for _ in range(IO_RETRIES):
            value = await self._request(line, expect)
            if not value.startswith(IO_ERROR):
                return value
            await asyncio.sleep(IO_RETRY_DELAY)
        raise PiSugarError(f"{line}: {value}")

    async def _request(self, line: str, expect: str) -> str:
        """Send one command and return its value. Reconnects and retries once."""
        async with self._lock:
            for attempt in (1, 2):
                try:
                    value = await self._request_once(line, expect)
                    self.connected = True
                    return value
                except PiSugarError:
                    raise
                except (OSError, ConnectionError, TimeoutError, asyncio.IncompleteReadError) as e:
                    await self._close_cmd()
                    if attempt == 2:
                        self.connected = False
                        raise PiSugarError(f"{line}: {e}") from e
            raise AssertionError("unreachable")

    async def get(self, name: str) -> str:
        return await self.request(f"get {name}", name)

    async def set(self, command: str, *args: str) -> None:
        line = " ".join([command, *args])
        value = await self.request(line, command)
        if value != "done":
            raise PiSugarError(f"{command}: {value}")

    # Typed helpers ----------------------------------------------------------------
    async def battery(self) -> float:
        return float(await self.get("battery"))

    async def plugged(self) -> bool:
        return (await self.get("battery_power_plugged")) == "true"

    async def get_bool(self, name: str) -> bool:
        return (await self.get(name)) == "true"

    async def rtc_time(self) -> datetime:
        return datetime.fromisoformat(await self.get("rtc_time"))

    async def alarm_set(self, when: datetime) -> None:
        """Arm the timed power-on (any weekday). `when` must be timezone-aware."""
        if when.tzinfo is None:
            raise ValueError("alarm time needs a UTC offset")
        await self.set("rtc_alarm_set", when.isoformat(timespec="seconds"), "127")

    async def alarm_disable(self) -> None:
        await self.set("rtc_alarm_disable")

    async def alarm_read(self) -> tuple[bool, datetime | None]:
        enabled = await self.get_bool("rtc_alarm_enabled")
        try:
            t = datetime.fromisoformat(await self.get("rtc_alarm_time"))
        except (ValueError, PiSugarError):
            t = None
        return enabled, t

    async def rtc_web(self) -> None:
        await self.set("rtc_web")

    async def rtc_to_pi(self) -> None:
        await self.set("rtc_rtc2pi")

    async def pi_to_rtc(self) -> None:
        await self.set("rtc_pi2rtc")


# The settings the camera depends on (§6.5), checked and re-applied at every start.
def required_settings(ctl_path: str = "/usr/local/bin/inkstant-ctl power-button") -> list[tuple[str, str, str]]:
    """(get name, expected value, set command line)."""
    return [
        ("soft_poweroff", "true", "set_soft_poweroff true"),
        ("soft_poweroff_shell", ctl_path, f"set_soft_poweroff_shell {ctl_path}"),
        ("auto_power_on", "false", "set_auto_power_on false"),
        ("anti_mistouch", "true", "set_anti_mistouch true"),
        ("safe_shutdown_level", "3", "set_safe_shutdown_level 3"),
        ("safe_shutdown_delay", "10", "set_safe_shutdown_delay 10"),
    ]


def _same(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    try:
        return float(actual) == float(expected)
    except ValueError:
        return False


async def ensure_settings(client: PiSugarClient, ctl_path: str, log=None) -> list[str]:
    """Check each required setting, fix any that differ, read them back. Returns problems."""
    problems = []
    for name, expected, set_line in required_settings(ctl_path):
        try:
            actual = await client.get(name)
            if _same(actual, expected):
                continue
            cmd, *args = set_line.split(" ", 1)
            await client.set(cmd, *args)
            again = await client.get(name)
            if not _same(again, expected):
                problems.append(f"{name} is {again!r}, wanted {expected!r}")
            elif log:
                log(f"pisugar: fixed {name}: {actual!r} → {expected!r}")
        except PiSugarError as e:
            problems.append(f"{name}: {e}")
    return problems


class FakePiSugarServer:
    """Speaks pisugar-server's socket protocol (tests, simulator)."""

    def __init__(self, sock_path: Path):
        self.sock_path = str(sock_path)
        self.values = {
            "model": "PiSugar 3", "firmware_version": "1.2.4", "version": "2.3.5",
            "battery": "80.0", "battery_power_plugged": "false", "battery_charging": "false",
            "rtc_time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "rtc_alarm_enabled": "false", "rtc_alarm_time": "2000-01-01T00:00:00.000+00:00",
            "soft_poweroff": "false", "soft_poweroff_shell": "", "auto_power_on": "false",
            "anti_mistouch": "true", "safe_shutdown_level": "0", "safe_shutdown_delay": "0",
            "battery_input_protect_enabled": "false",
        }
        self.log: list[str] = []
        self.rtc_offset = 0.0
        self.fail_next: str | None = None
        self.io_errors: dict[str, int] = {}  # value or command → I2C errors to give first
        self._server = None
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        Path(self.sock_path).parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            Path(self.sock_path).unlink()
        self._server = await asyncio.start_unix_server(self._client, path=self.sock_path)

    async def stop(self) -> None:
        for w in self._writers:
            with contextlib.suppress(Exception):
                w.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        with contextlib.suppress(FileNotFoundError):
            Path(self.sock_path).unlink()

    async def tap(self, word: str) -> None:
        for w in list(self._writers):
            with contextlib.suppress(Exception):
                w.write((word + "\n").encode())
                await w.drain()

    def _reply(self, req: str) -> str:
        self.log.append(req)
        parts = req.split(" ")
        if self.fail_next and parts[0] == self.fail_next:
            self.fail_next = None
            return f"{parts[0]}: I2C error"
        key = parts[1] if parts[0] == "get" and len(parts) > 1 else parts[0]
        if self.io_errors.get(key):
            self.io_errors[key] -= 1
            return f"{key}: I/O error: Remote I/O error (os error 121)"  # as pisugar-server words it
        if parts[0] == "get" and len(parts) > 1:
            name = parts[1]
            if name == "rtc_time":
                return f"rtc_time: {datetime.now().astimezone().isoformat(timespec='milliseconds')}"
            if name in self.values:
                return f"{name}: {self.values[name]}"
            return f"error: unrecognized subcommand '{name}'\nHint: use 'help' to see all available commands"
        cmd = parts[0]
        args = parts[1:]
        simple = {
            "set_soft_poweroff": "soft_poweroff", "set_auto_power_on": "auto_power_on",
            "set_anti_mistouch": "anti_mistouch", "set_safe_shutdown_level": "safe_shutdown_level",
            "set_safe_shutdown_delay": "safe_shutdown_delay",
            "set_battery_input_protect": "battery_input_protect_enabled",
        }
        if cmd in simple and args:
            self.values[simple[cmd]] = args[0]
            return f"{cmd}: done"
        if cmd == "set_soft_poweroff_shell":
            self.values["soft_poweroff_shell"] = " ".join(args)
            return f"{cmd}: done"
        if cmd == "rtc_alarm_set" and len(args) == 2:
            try:
                datetime.fromisoformat(args[0])
            except ValueError:
                return f"error: invalid value '{args[0]}'"
            self.values["rtc_alarm_enabled"] = "true"
            self.values["rtc_alarm_time"] = args[0]
            return f"{cmd}: done"
        if cmd == "rtc_alarm_disable":
            self.values["rtc_alarm_enabled"] = "false"
            return f"{cmd}: done"
        if cmd in ("rtc_web", "rtc_rtc2pi", "rtc_pi2rtc"):
            return f"{cmd}: done"
        return f"error: unrecognized subcommand '{cmd}'\nHint: use 'help' to see all available commands"

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.append(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                req = line.decode().strip()
                if not req:
                    continue
                writer.write((self._reply(req) + "\n").encode())
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(ValueError):
                self._writers.remove(writer)
            with contextlib.suppress(Exception):
                writer.close()
