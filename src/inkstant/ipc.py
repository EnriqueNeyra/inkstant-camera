"""The camera app's control socket (SPEC.md §12).

One JSON object per line. The camera app serves; the gallery and inkstant-ctl call.
"""

from __future__ import annotations

import asyncio
import contextlib
import grp
import itertools
import json
import os
import pwd
import socket
import struct
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

MAX_MESSAGE = 64 * 1024
REQUEST_TIMEOUT = 5.0
# Commands that wait on Wi-Fi, the network or the hardware get longer. share.start can scan (12 s),
# join a network (25 s) or start the camera's Wi-Fi (20 s), then wait for the gallery (10 s).
COMMAND_TIMEOUTS = {"share.start": 75.0, "selftest.run": 30.0, "update.check": 15.0, "update.start": 30.0}
WEB_USER = "inkstant-web"

# Commands the gallery (user inkstant-web) may use; root may use all (§12).
GALLERY_COMMANDS = frozenset({
    "status", "photos.delete", "photos.show", "settings.get", "settings.set", "time.set",
    "wifi.list", "wifi.home.set", "wifi.home.remove", "share.stop", "share.activity",
    "update.check", "update.start", "selftest.get", "selftest.run",
})
ROOT_ONLY_COMMANDS = frozenset({
    "power.button", "shutdown", "share.start", "dev.set", "regen_epd", "update.report",
})
ALL_COMMANDS = GALLERY_COMMANDS | ROOT_ONLY_COMMANDS


def timeout_for(cmd: str) -> float:
    return COMMAND_TIMEOUTS.get(cmd, REQUEST_TIMEOUT)


class IpcError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


Handler = Callable[[str, dict[str, Any], str], Awaitable[Any]]


def peer_uid(sock: socket.socket) -> int | None:
    """The calling process's user id, from the kernel (Linux SO_PEERCRED)."""
    if sys.platform.startswith("linux"):
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
        return uid
    return None


def _web_uid() -> int | None:
    try:
        return pwd.getpwnam(WEB_USER).pw_uid
    except KeyError:
        return None


class ControlServer:
    def __init__(self, path: Path, handler: Handler, *, trust_all: bool = False, log=print):
        self.path = Path(path)
        self.handler = handler
        self.trust_all = trust_all
        self.log = log
        self.web_uid = _web_uid()
        self._server: asyncio.base_events.Server | None = None

    def role_for(self, uid: int | None) -> str | None:
        if self.trust_all:
            return "root"
        if uid == 0:
            return "root"
        if uid is not None and uid == self.web_uid:
            return "gallery"
        return None

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self._server = await asyncio.start_unix_server(self._client, path=str(self.path), limit=MAX_MESSAGE)
        if os.geteuid() == 0:
            try:
                gid = grp.getgrnam(WEB_USER).gr_gid
                os.chown(self.path, 0, gid)
            except KeyError:
                pass
        os.chmod(self.path, 0o660)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        try:
            role = self.role_for(peer_uid(sock) if sock is not None else None)
        except OSError:
            role = None
        try:
            while True:
                try:
                    line = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    await self._send(writer, {"id": None, "ok": False,
                                              "error": {"code": "invalid", "message": "Message too long."}})
                    return
                if not line:
                    return
                reply = await self._handle_line(line, role)
                await self._send(writer, reply)
        except (ConnectionError, asyncio.IncompleteReadError):
            return
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _send(self, writer: asyncio.StreamWriter, obj: dict) -> None:
        writer.write((json.dumps(obj, default=str) + "\n").encode())
        await writer.drain()

    async def _handle_line(self, line: bytes, role: str | None) -> dict:
        try:
            req = json.loads(line)
            rid = req.get("id")
            cmd = req.get("cmd")
            args = req.get("args") or {}
            if not isinstance(cmd, str) or not isinstance(args, dict):
                raise ValueError
        except (ValueError, AttributeError):
            return {"id": None, "ok": False, "error": {"code": "invalid", "message": "Bad request."}}
        if role is None:
            return {"id": rid, "ok": False, "error": {"code": "denied", "message": "Not allowed."}}
        if cmd not in ALL_COMMANDS:
            return {"id": rid, "ok": False, "error": {"code": "unknown", "message": f"Unknown command {cmd!r}."}}
        if role == "gallery" and cmd not in GALLERY_COMMANDS:
            return {"id": rid, "ok": False, "error": {"code": "denied", "message": "Not allowed."}}
        try:
            result = await asyncio.wait_for(self.handler(cmd, args, role), timeout_for(cmd))
            return {"id": rid, "ok": True, "result": result}
        except IpcError as e:
            return {"id": rid, "ok": False, "error": {"code": e.code, "message": e.message}}
        except TimeoutError:
            return {"id": rid, "ok": False, "error": {"code": "timeout", "message": "The camera didn't answer in time."}}
        except Exception as e:  # never let one bad request kill the server
            self.log(f"control: {cmd} failed: {e!r}")
            return {"id": rid, "ok": False, "error": {"code": "internal", "message": "Something went wrong on the camera."}}


_ids = itertools.count(1)


def call(path: Path, cmd: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
    """Send one request and wait for its reply (blocking): by default a second longer than the
    camera app gives the command. Raises IpcError."""
    if timeout is None:
        timeout = timeout_for(cmd) + 1
    rid = next(_ids)
    data = (json.dumps({"id": rid, "cmd": cmd, "args": args or {}}) + "\n").encode()
    if len(data) > MAX_MESSAGE:
        raise IpcError("invalid", "Request too long.")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(data)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 4 * MAX_MESSAGE:
                    raise IpcError("invalid", "Reply too long.")
    except FileNotFoundError as e:
        raise IpcError("unavailable", "The camera app isn't running.") from e
    except ConnectionRefusedError as e:
        raise IpcError("unavailable", "The camera app isn't running.") from e
    except TimeoutError as e:
        raise IpcError("timeout", "The camera didn't answer in time.") from e
    except OSError as e:
        raise IpcError("unavailable", f"Can't reach the camera app: {e}") from e
    try:
        reply = json.loads(buf)
    except ValueError as e:
        raise IpcError("invalid", "Bad reply from the camera app.") from e
    if reply.get("ok"):
        return reply.get("result")
    err = reply.get("error") or {}
    raise IpcError(err.get("code", "error"), err.get("message", "Unknown error."))
