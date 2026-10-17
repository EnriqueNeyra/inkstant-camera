"""inkstant-ctl: status, self-test, sharing, shutdown and helpers (SPEC.md §17). Standard library only."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys

from inkstant import ipc, selftest
from inkstant.paths import Paths

POWER_BUTTON_TIMEOUT = 3.0


def _call(paths: Paths, cmd: str, args: dict | None = None, timeout: float | None = None):
    return ipc.call(paths.control_sock, cmd, args or {}, timeout=timeout)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def cmd_power_button(paths: Paths) -> int:
    """Run by pisugar-server on a PWR long press, and by its low-battery backup.

    pisugar-server waits for this to finish, so it must return at once: it only asks the
    camera app, which shuts down on its own. If the app doesn't answer, power off directly.
    """
    try:
        r = _call(paths, "power.button", timeout=POWER_BUTTON_TIMEOUT)
        print(r.get("action", ""))
        return 0
    except ipc.IpcError as e:
        print(f"camera app didn't answer ({e.message}); powering off", file=sys.stderr)
        subprocess.run(["systemctl", "poweroff", "--no-block"], check=False)
        return 0


def _pisugar_cmd(paths: Paths, line: str, timeout: float = 3.0) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(paths.pisugar_sock))
        s.sendall((line + "\n").encode())
        return s.recv(4096).decode(errors="replace").strip()


def cmd_failsafe(paths: Paths) -> int:
    """inkstant-failsafe.service: the app crash-looped. Disable the slideshow alarm and power off."""
    try:
        print(_pisugar_cmd(paths, "rtc_alarm_disable"))
    except OSError as e:
        print(f"failsafe: {e}", file=sys.stderr)
    subprocess.run(["systemctl", "poweroff", "--no-block"], check=False)
    return 0


def cmd_selftest(paths: Paths, install: bool) -> int:
    if not install:
        try:
            r = _call(paths, "selftest.run")
            _report(r)
            return 0 if r.get("passed") else 1
        except ipc.IpcError:
            pass  # the app isn't running: check what we can from here
    from inkstant import device

    fam = device.load(paths.device_toml).family
    checks = selftest.run(paths, family=fam, install=install)
    r = selftest.summary(checks)
    _report(r)
    return 0 if r["passed"] else 1


def _report(r: dict) -> None:
    for c in r.get("checks", []):
        mark = "ok  " if c["ok"] else ("FAIL" if c["critical"] else "warn")
        line = f"[{mark}] {c['name']}"
        if c.get("detail"):
            line += f": {c['detail']}"
        print(line)
        if not c["ok"] and c.get("hint"):
            print(f"       {c['hint']}")
    print("PASSED" if r.get("passed") else "FAILED: " + ", ".join(r.get("failed", [])))


def cmd_rollback(paths: Paths) -> int:
    releases = paths.opt / "releases"
    current = (paths.opt / "current").resolve()
    found = sorted((p for p in releases.iterdir() if p.is_dir() and not p.name.startswith(".")),
                   key=lambda p: p.stat().st_mtime)
    previous = [p for p in found if p.resolve() != current]
    if not previous:
        print("No previous release to go back to.", file=sys.stderr)
        return 1
    target = previous[-1]
    tmp = paths.opt / "current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, paths.opt / "current")
    print(f"Switched to {target.name}; restarting the camera app.")
    subprocess.run(["systemctl", "restart", "inkstant.service"], check=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    paths = Paths.default()
    p = argparse.ArgumentParser(prog="inkstant-ctl", description="Inkstant camera control")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show the camera app's state")
    st = sub.add_parser("selftest", help="Run the setup check")
    st.add_argument("--install", action="store_true", help="Only what can pass before the first reboot")
    sh = sub.add_parser("share", help="Share mode on or off")
    sh.add_argument("state", choices=["on", "off"])
    sh.add_argument("--hotspot", action="store_true", help="Use the camera's own Wi-Fi even at home")
    sd = sub.add_parser("shutdown", help="Clean shutdown through the camera app")
    sd.add_argument("--reboot", action="store_true")
    dv = sub.add_parser("dev", help="Developer mode")
    dv.add_argument("state", choices=["on", "off"])
    sub.add_parser("regen-epd", help="Rebuild every screen image")
    sub.add_parser("rollback", help="Go back to the previous release")
    sub.add_parser("power-button", help="(run by pisugar-server)")
    sub.add_parser("failsafe", help="(run by inkstant-failsafe.service)")
    ur = sub.add_parser("update-report", help="(run by update.sh)")
    ur.add_argument("state", choices=["running", "failed", "done"])
    ur.add_argument("step", nargs="?")
    ur.add_argument("message", nargs="?")
    a = p.parse_args(argv)

    try:
        if a.cmd == "status":
            _print(_call(paths, "status"))
        elif a.cmd == "selftest":
            return cmd_selftest(paths, a.install)
        elif a.cmd == "share":
            if a.state == "on":
                _print(_call(paths, "share.start", {"force_hotspot": a.hotspot}))
            else:
                _call(paths, "share.stop")
        elif a.cmd == "shutdown":
            _call(paths, "shutdown", {"reason": "system", "reboot": a.reboot})
        elif a.cmd == "dev":
            _call(paths, "dev.set", {"enabled": a.state == "on"})
        elif a.cmd == "regen-epd":
            _print(_call(paths, "regen_epd"))
        elif a.cmd == "rollback":
            return cmd_rollback(paths)
        elif a.cmd == "power-button":
            return cmd_power_button(paths)
        elif a.cmd == "failsafe":
            return cmd_failsafe(paths)
        elif a.cmd == "update-report":
            _call(paths, "update.report", {"state": a.state, "step": a.step, "message": a.message})
    except ipc.IpcError as e:
        print(f"inkstant-ctl: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
