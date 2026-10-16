#!/usr/bin/env python3
"""Shutter delay at each frame rate (SPEC.md §19.5, §19.6 "Camera").

For each press: the time from the press to the start of the captured frame's exposure
(SensorTimestamp − ExposureTime, "moment captured"), and to the frame arriving ("frame in hand").
Picks the lowest frame rate that keeps the frame in hand within 0.5 s (§6.4).

    sudo systemctl stop inkstant
    sudo python3 tools/bench/shutter_latency.py                 # press the real shutter button
    sudo python3 tools/bench/shutter_latency.py --auto          # no button: timed software presses
    sudo python3 tools/bench/shutter_latency.py --fps 5 10 14 --presses 50
"""

from __future__ import annotations

import argparse
import queue
import random
import sys
import time

import _common as c

TARGET_MS = 500


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fps", type=int, nargs="+", default=[5, 10, 14])
    ap.add_argument("--presses", type=int, default=50)
    ap.add_argument("--auto", action="store_true", help="software presses every 0.5 to 1.5 s")
    a = ap.parse_args()
    c.require_root()
    c.require_app_stopped()

    from inkstant.hw import camera as cam_mod
    from inkstant.hw import gpio

    presses: queue.Queue[int] = queue.Queue()
    button = None
    if not a.auto:
        from gpiozero import Button

        button = Button(gpio.SHUTTER, pull_up=True, bounce_time=0.03)
        button.when_pressed = lambda: presses.put(time.monotonic_ns())

    cam = cam_mod.Camera(c.device(), log=print)
    cam.open()
    result: dict = {"auto": a.auto, "rates": {}}
    for fps in sorted(a.fps):
        cam.set_fps(fps)
        for _ in range(int(fps)):  # a second of frames at the new rate
            cam.picam2.capture_metadata(wait=2)
        while not presses.empty():
            presses.get_nowait()
        print(f"\n{fps} fps: " + ("taking" if a.auto else "press the shutter") + f" {a.presses} times"
              + ("" if a.auto else " (about one press a second)"))
        captured, in_hand = [], []
        for i in range(a.presses):
            if a.auto:
                time.sleep(random.uniform(0.5, 1.5))
                press = time.monotonic_ns()
            else:
                try:
                    press = presses.get(timeout=120)
                except queue.Empty:
                    print("  no press for 2 minutes; moving on")
                    break
            req = cam.picam2.capture_request(flush=press, wait=5)
            arrived = time.monotonic_ns()
            md = req.get_metadata()
            req.release()
            start = md["SensorTimestamp"] - 1000 * md["ExposureTime"]
            captured.append((start - press) / 1e6)
            in_hand.append((arrived - press) / 1e6)
            print(f"  {i + 1:3d}: captured +{captured[-1]:6.0f} ms, in hand +{in_hand[-1]:6.0f} ms, "
                  f"exposure {md['ExposureTime'] / 1000:.1f} ms")
        cma = c.meminfo().get("CmaFree")
        meter = "" if a.auto else c.ask(f"  USB meter reading at {fps} fps, in mA (Enter to skip): ").strip()
        result["rates"][fps] = {"captured_ms": c.stats(captured), "in_hand_ms": c.stats(in_hand),
                                "raw_captured_ms": captured, "raw_in_hand_ms": in_hand,
                                "cma_free_kb": cma, "current_ma": meter or None}
    cam.close()
    if button:
        button.close()

    print("\nSummary (target: frame in hand ≤ 500 ms at p90)")
    best = None
    for fps, r in result["rates"].items():
        ok = r["in_hand_ms"].get("p90", 1e9) <= TARGET_MS
        print(f"  {fps:2d} fps: moment captured {c.fmt_stats(r['captured_ms'])}")
        print(f"          frame in hand    {c.fmt_stats(r['in_hand_ms'])}  {'OK' if ok else 'too slow'}"
              + (f", {r['current_ma']} mA" if r["current_ma"] else ""))
        if ok and best is None:
            best = fps
    if best:
        print(f"\nLowest frame rate that meets the target: {best} fps. Set it with camera_fps = {best} "
              "in /etc/inkstant/device.toml (and update CAMERA_FPS in hw/camera.py for everyone).")
    else:
        print("\nNo tested frame rate meets the target.")
    result["lowest_ok_fps"] = best
    c.save("shutter_latency", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
