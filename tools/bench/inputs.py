#!/usr/bin/env python3
"""Shutter button, module detect, BUSY, and the status light (SPEC.md §19.5, §6.1, §6.2, §1.3).

    sudo systemctl stop inkstant
    sudo python3 tools/bench/inputs.py            # log edges for 60 s: press, slide a module in and out
    sudo python3 tools/bench/inputs.py --light    # step through every light pattern and cue
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import _common as c

LED = Path("/sys/class/leds/status")


def watch(seconds: float) -> dict:
    from gpiozero import Button, DigitalInputDevice

    from inkstant.hw import gpio

    t0 = time.monotonic()
    events: list[dict] = []

    def log(name, value):
        t = time.monotonic() - t0
        events.append({"t_s": round(t, 4), "pin": name, "value": value})
        print(f"  {t:8.3f} s  {name:8s} {value}")

    shutter = Button(gpio.SHUTTER, pull_up=True, bounce_time=None)  # no debounce: see the raw bounces
    detect = DigitalInputDevice(gpio.DETECT, pull_up=True)
    busy = DigitalInputDevice(gpio.BUSY, pull_up=False)
    shutter.when_pressed = lambda: log("shutter", "pressed")
    shutter.when_released = lambda: log("shutter", "released")
    detect.when_activated = lambda: log("detect", "module in")
    detect.when_deactivated = lambda: log("detect", "module out")
    busy.when_activated = lambda: log("busy", "high")
    busy.when_deactivated = lambda: log("busy", "low")
    print(f"Now: shutter {'pressed' if shutter.is_pressed else 'up'}, "
          f"detect {'module in' if detect.is_active else 'no module'}, BUSY {'high' if busy.value else 'low'}")
    print(f"Logging edges for {seconds:.0f} s. Press the shutter a few times; slide a module in and out.")
    time.sleep(seconds)
    for d in (shutter, detect, busy):
        d.close()
    presses = [e for e in events if e["pin"] == "shutter" and e["value"] == "pressed"]
    bounces = sum(1 for a, b in zip(presses, presses[1:], strict=False) if b["t_s"] - a["t_s"] < 0.03)
    print(f"\n{len(presses)} press edges, {bounces} within 30 ms of the previous one (bounce; the app debounces 30 ms)")
    return {"events": events, "press_edges": len(presses), "bounces_under_30ms": bounces}


def light() -> dict:
    from inkstant.hw.led import CUES, STEADY, SysfsLed

    if not LED.exists():
        sys.exit(f"{LED} is missing: the gpio-led overlay isn't loaded (check config.txt and reboot).")
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
    for name, (pattern, repeat, _secs) in CUES.items():
        led.set_level(1)
        time.sleep(1)
        led.set_pattern(pattern, repeat)
        answers[f"cue {name}"] = c.ask(f"  cue '{name}' over 'ready'. Describe it, or Enter for next: ").strip()
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
