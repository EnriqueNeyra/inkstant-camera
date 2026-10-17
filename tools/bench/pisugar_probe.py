#!/usr/bin/env python3
"""PiSugar 3: socket, settings, button taps, clock, alarm and watchdog.

    sudo systemctl stop inkstant
    sudo python3 tools/bench/pisugar_probe.py                    # everything except the watchdog reset
    sudo python3 tools/bench/pisugar_probe.py --taps 30          # listen for BTN taps for 30 s
    sudo python3 tools/bench/pisugar_probe.py --watchdog-reset   # stop feeding: the Pi must power-cycle

The alarm is put back the way it was found.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import _common as c

GETS = ["model", "firmware_version", "battery", "battery_v", "battery_charging", "battery_power_plugged",
        "battery_allow_charging", "battery_charging_range", "rtc_time", "rtc_alarm_enabled", "rtc_alarm_time",
        "safe_shutdown_level", "safe_shutdown_delay", "auto_power_on", "soft_poweroff", "soft_poweroff_shell",
        "anti_mistouch", "temperature"]
MIN_FIRMWARE = (1, 2, 4)


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def sh(*args: str) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"({e})"


async def probe(a) -> dict:
    from inkstant.hw.pisugar import PiSugarClient, PiSugarError, required_settings
    from inkstant.paths import Paths

    sock = Paths.default().pisugar_sock
    result: dict = {"socket": str(sock), "values": {}, "problems": []}
    problems = result["problems"]
    try:
        mode = stat.S_IMODE(os.stat(sock).st_mode)
        result["socket_mode"] = oct(mode)
        print(f"Socket {sock}: mode {oct(mode)}" + ("" if mode == 0o600 else "  (expected 0600)"))
        if mode != 0o600:
            problems.append(f"socket mode {oct(mode)}")
    except OSError as e:
        problems.append(f"no socket: {e}")
        print(f"FAIL: {sock}: {e}. Is pisugar-server running? systemctl status pisugar-server")
        return result
    listening = [ln for ln in sh("ss", "-ltnp").splitlines() if "pisugar" in ln]
    result["tcp_listeners"] = listening
    print("TCP listeners: " + ("none (good)" if not listening else "; ".join(listening)))
    if listening:
        problems.append("pisugar-server listens on TCP")

    taps: list[tuple[float, str]] = []
    t0 = time.monotonic()
    client = PiSugarClient(sock, on_tap=lambda t: (taps.append((time.monotonic() - t0, t)),
                                                    print(f"  tap: {t}")), log=print)
    if not await client.connect():
        problems.append("can't connect")
        return result
    for name in GETS:
        try:
            v = await client.get(name)
        except PiSugarError as e:
            v = f"ERROR {e}"
        result["values"][name] = v
        print(f"  {name:24s} {v}")
    fw = result["values"].get("firmware_version", "")
    if version_tuple(fw) < MIN_FIRMWARE:
        problems.append(f"firmware {fw} is older than 1.2.4 (the delayed power-off needs it)")
    for name, expected, _ in required_settings():
        actual = result["values"].get(name)
        if actual is not None and actual != expected and actual.replace(".0", "") != expected:
            print(f"  note: {name} is {actual!r}; the camera app sets it to {expected!r} when it starts")

    # Alarm: set one a day out, read it back, then restore.
    try:
        was_on, was_at = await client.alarm_read()
        when = (datetime.now().astimezone() + timedelta(days=1)).replace(microsecond=0)
        await client.alarm_set(when)
        on, at = await client.alarm_read()
        # The alarm is a time of day; compare in UTC, since the board may report another offset.
        ok = on and at is not None and (at.tzinfo is None or (
            at.astimezone(UTC).time().replace(second=0) == when.astimezone(UTC).time().replace(second=0)))
        print(f"Alarm set {when.isoformat()} → read back enabled={on} at {at}: {'OK' if ok else 'MISMATCH'}")
        if not ok:
            problems.append("alarm read-back mismatch")
        if was_on and was_at:
            await client.alarm_set(was_at if was_at.tzinfo else was_at.astimezone())
        else:
            await client.alarm_disable()
        print(f"Alarm restored ({'enabled at ' + str(was_at) if was_on else 'disabled'})")
    except PiSugarError as e:
        problems.append(f"alarm: {e}")

    if a.taps:
        print(f"\nPress BTN now: single, double and long taps, for {a.taps} s")
        await asyncio.sleep(a.taps)
        result["taps"] = taps
        kinds = {t for _, t in taps}
        print(f"Heard: {', '.join(t for _, t in taps) or 'nothing'}")
        if not {"single", "double"} <= kinds:
            problems.append("didn't hear both a single and a double tap")
    await client.close()
    return result


def watchdog(a, result: dict) -> None:
    from inkstant.hw.pisugar_i2c import (
        ADDR,
        BIT_ENABLE,
        REG_PROTECT,
        REG_TIMEOUT,
        REG_WATCHDOG,
        Watchdog,
        WatchdogError,
    )

    wd = Watchdog(log=print)
    try:
        before = {hex(r): wd._r(r) for r in (REG_WATCHDOG, REG_TIMEOUT, REG_PROTECT)}
        print(f"\nWatchdog registers at {hex(ADDR)}: {before}")
        wd.enable()
        print(f"  enabled: 0x06={wd._r(REG_WATCHDOG):#04x} 0x07={wd._r(REG_TIMEOUT)} (×2 s)")
        for _ in range(3):
            wd.feed()
            time.sleep(1)
        if a.watchdog_reset:
            if c.ask("  Stop feeding now? The PiSugar should cut power and restart the Pi within about "
                     "60 s. Type yes: ").strip() != "yes":
                wd.disable()
                return
            os.sync()
            t0 = time.monotonic()
            while True:
                print(f"  not feeding: {time.monotonic() - t0:4.0f} s", flush=True)
                time.sleep(5)
        wd.disable()
        after = wd._r(REG_WATCHDOG)
        print(f"  disabled: 0x06={after:#04x} ({'OK' if not after & BIT_ENABLE else 'STILL ON'})")
        result["watchdog"] = {"before": before, "after_disable": after}
    except (OSError, WatchdogError) as e:
        result["problems"].append(f"watchdog: {e}")
        print(f"  watchdog FAILED: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--taps", type=int, default=0, help="listen for BTN taps for this many seconds")
    ap.add_argument("--watchdog-reset", action="store_true")
    a = ap.parse_args()
    c.require_root()
    c.require_app_stopped()
    print("I2C bus 1:\n" + sh("i2cdetect", "-y", "1"))
    rtc = sh("cat", "/sys/class/rtc/rtc0/name").strip()
    print(f"Kernel RTC: {rtc or 'none'}; {sh('hwclock', '-r').strip()}")
    result = asyncio.run(probe(a))
    result["kernel_rtc"] = rtc
    watchdog(a, result)
    print("\nPASS" if not result["problems"] else "\nPROBLEMS: " + "; ".join(result["problems"]))
    c.save("pisugar_probe", result)
    return 0 if not result["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
