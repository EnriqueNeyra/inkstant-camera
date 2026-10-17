"""Operating-system actions: power, units, clock, systemd notify, model (SPEC.md §6.7, §13)."""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import time
from pathlib import Path


def sd_notify(message: str) -> bool:
    """systemd's sd_notify, standard library only. False when not run by systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.connect(addr)
            s.sendall(message.encode())
        return True
    except OSError:
        return False


class System:
    def __init__(self, root: Path = Path("/"), log=None, run=subprocess.run):
        self.root = Path(root)
        self.log = log or (lambda *_: None)
        self._run = run

    def run(self, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess:
        return self._run(list(args), capture_output=True, text=True, timeout=timeout)

    # Power ------------------------------------------------------------------------
    def poweroff(self) -> None:
        self.run("systemctl", "poweroff", "--no-block")

    def reboot(self) -> None:
        self.run("systemctl", "reboot", "--no-block")

    def stopping_for(self) -> str | None:
        """Why systemd is stopping the camera app, from its queued jobs: "reboot", "poweroff",
        or None when only the service is stopping (a restart, an install)."""
        try:
            jobs = self.run("systemctl", "list-jobs", "--no-legend", timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return "poweroff"  # can't tell: take it as the camera turning off
        if "reboot.target" in jobs or "kexec.target" in jobs:
            return "reboot"
        if any(t in jobs for t in ("poweroff.target", "halt.target", "shutdown.target")):
            return "poweroff"
        return None

    # Units -------------------------------------------------------------------------
    def start_unit(self, unit: str) -> bool:
        return self.run("systemctl", "start", unit, timeout=30).returncode == 0

    def stop_unit(self, unit: str) -> bool:
        return self.run("systemctl", "stop", unit, timeout=30).returncode == 0

    def notify(self, message: str) -> None:
        sd_notify(message)

    # Clock ---------------------------------------------------------------------------
    def uptime(self) -> float:
        try:
            return float((self.root / "proc/uptime").read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return time.monotonic()

    def rtc0_is_pisugar(self) -> bool:
        try:
            name = (self.root / "sys/class/rtc/rtc0/name").read_text()
        except OSError:
            return False
        return "ds1307" in name or "ds3231" in name

    def rtc0_epoch(self) -> int | None:
        try:
            return int((self.root / "sys/class/rtc/rtc0/since_epoch").read_text().strip())
        except (OSError, ValueError):
            return None

    def set_clock(self, epoch: float) -> None:
        time.clock_settime(time.CLOCK_REALTIME, epoch)

    def set_timezone(self, tz: str) -> bool:
        if not tz or ".." in tz or tz.startswith("/") or not (self.root / "usr/share/zoneinfo" / tz).is_file():
            return False
        ok = self.run("timedatectl", "set-timezone", tz).returncode == 0
        if ok:
            os.environ["TZ"] = tz
            time.tzset()
        return ok

    # Board -----------------------------------------------------------------------------
    def model(self) -> str:
        try:
            return (self.root / "proc/device-tree/model").read_text().rstrip("\x00\n")
        except OSError:
            return "unknown"

    def usb_hub_off(self) -> bool:
        """3B and 3B+ only: unbind the on-board USB hub and Ethernet chip (§13.6)."""
        path = self.root / "sys/bus/usb/drivers/usb/unbind"
        if not (self.root / "sys/bus/usb/devices/1-1").exists():
            return False
        try:
            path.write_text("1-1")
            return True
        except OSError as e:
            self.log(f"usb hub off: {e}")
            return False

    def journal(self, max_bytes: int = 2_000_000) -> bytes:
        """This boot's and the previous boot's logs, at most `max_bytes` (the newest kept)."""
        out = b""
        for boot in ("-1", "0"):
            with contextlib.suppress(Exception):
                p = subprocess.run(["journalctl", "-b", boot, "--no-pager", "-o", "short-iso"],
                                   capture_output=True, timeout=20)
                out += p.stdout
        return out[-max_bytes:]


class FakeSystem:
    def __init__(self):
        self.calls: list[tuple] = []
        self.clock_offset = 0.0
        self.tz = "UTC"
        self.units: set[str] = set()
        self.rtc_epoch: int | None = int(time.time())
        self._model = "Raspberry Pi 3 Model A Plus Rev 1.1 (simulated)"
        self._t0 = time.monotonic()
        self.stopping: str | None = "poweroff"  # what an outside stop is for (stopping_for)

    def poweroff(self) -> None:
        self.calls.append(("poweroff",))

    def reboot(self) -> None:
        self.calls.append(("reboot",))

    def stopping_for(self) -> str | None:
        return self.stopping

    def start_unit(self, unit: str) -> bool:
        self.units.add(unit)
        self.calls.append(("start", unit))
        return True

    def stop_unit(self, unit: str) -> bool:
        self.units.discard(unit)
        self.calls.append(("stop", unit))
        return True

    def notify(self, message: str) -> None:
        self.calls.append(("notify", message))

    def uptime(self) -> float:
        return 5.0 + time.monotonic() - self._t0

    def rtc0_is_pisugar(self) -> bool:
        return self.rtc_epoch is not None

    def rtc0_epoch(self) -> int | None:
        return self.rtc_epoch

    def set_clock(self, epoch: float) -> None:
        self.clock_offset = epoch - time.time()
        self.calls.append(("set_clock", epoch))

    def set_timezone(self, tz: str) -> bool:
        self.tz = tz
        self.calls.append(("timezone", tz))
        return True

    def model(self) -> str:
        return self._model

    def usb_hub_off(self) -> bool:
        return False

    def journal(self, max_bytes: int = 2_000_000) -> bytes:
        return b"(simulator: no journal)\n"
