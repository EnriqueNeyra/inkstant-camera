#!/usr/bin/env python3
"""Print detect, BUSY, and the status light (the Pi's green ACT light).

    sudo systemctl stop inkstant
    sudo python3 tools/bench/inputs.py            # log edges for 60 s: take the print off and put it back on
    sudo python3 tools/bench/inputs.py --light    # step through every light pattern
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import _common as c

LED = Path("/sys/class/leds/ACT")


def watch(seconds: float) -> dict:
    from gpiozero import DigitalInputDevice

    from inkstant.hw import gpio

    t0 = time.monotonic()
    events: list[dict] = []

    def log(name, value):
        t = time.monotonic() - t0
        events.append({"t_s": round(t, 4), "pin": name, "value": value})
        print(f"  {t:8.3f} s  {name:8s} {value}")

    detect = DigitalInputDevice(gpio.DETECT, pull_up=False)
    busy = DigitalInputDevice(gpio.BUSY, pull_up=False)
    detect.when_activated = lambda: log("detect", "print on")
    detect.when_deactivated = lambda: log("detect", "print off")
    busy.when_activated = lambda: log("busy", "high")
    busy.when_deactivated = lambda: log("busy", "low")
    print(f"Now: detect {'print on' if detect.is_active else 'no print'}, BUSY {'high' if busy.value else 'low'}")
    print(f"Logging edges for {seconds:.0f} s. Take the print off and put it back on a few times.")
    time.sleep(seconds)
    for d in (detect, busy):
        d.close()
    changes = sum(1 for e in events if e["pin"] == "detect")
    print(f"\n{changes} print-detect changes")
    return {"events": events, "detect_changes": changes}


def light() -> dict:
    from inkstant.hw.led import STEADY, SysfsLed

    if not LED.exists():
        sys.exit(f"{LED} is missing: update Raspberry Pi OS, then reboot.")
    led = SysfsLed(LED)
    print(f"Triggers: {' '.join(led.triggers())}")
    print(f"Pattern trigger available: {led.has_pattern()} (without it the app blinks in software)")
    if not led.has_pattern():
        try:
            import subprocess

            subprocess.run(["modprobe", "ledtrig-pattern"], check=False)
            led._has_pattern = None
            print(f"After modprobe ledtrig-pattern: {led.has_pattern()}")
        except OSError:
            pass
    answers = {}
    for name, (trigger, value) in STEADY.items():
        if trigger == "timer":
            led.set_timer()
        elif trigger == "pattern":
            led.set_pattern(value, -1)
        else:
            led.set_level(value)
        answers[name] = c.ask(f"  showing '{name}'. Describe it, or Enter for next: ").strip()
    led.set_level(1)
    time.sleep(1)
    led.set_level(0)
    time.sleep(0.4)
    led.set_level(1)
    answers["wink"] = c.ask("  that was the shutter wink (off 0.4 s). Visible? ").strip()
    led.set_timer()
    print("Left on the boot-time timer blink.")
    return {"triggers": led.triggers(), "answers": answers}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--light", action="store_true")
    ap.add_argument("--seconds", type=float, default=60)
    a = ap.parse_args()
    c.require_root()
    c.require_app_stopped()
    if a.light:
        c.save("inputs-light", light())
    else:
        c.save("inputs", watch(a.seconds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
