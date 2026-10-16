#!/usr/bin/env python3
"""Display driver: show a test frame and time each step (SPEC.md §19.5, §6.3).

    sudo systemctl stop inkstant
    sudo python3 tools/bench/epd_show.py                     # the six-colour test chart
    sudo python3 tools/bench/epd_show.py --epd /var/lib/inkstant/epd/000001_20261016-120000.epd
    sudo python3 tools/bench/epd_show.py --white             # the white screen used at power-off
    sudo python3 tools/bench/epd_show.py --abort-after 8     # pull the module out mid-develop
    sudo python3 tools/bench/epd_show.py --probe             # reset only: does a module answer?

With no module in, the driver must give up within about 2 s (ModuleMissing), never hang.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

import _common as c

WAITS = ["reset", "init", "power on", "refresh", "power off"]


def chart_frame() -> bytes:
    """Six colour bars, a white band and an orientation mark, using the palette's own indices."""
    import numpy as np
    from PIL import Image, ImageDraw

    from inkstant.imaging import palette, pipeline

    img = Image.new("P", (600, 400), 1)
    img.putpalette([v for rgb in palette.RGB for v in rgb] + [0, 0, 0] * (256 - len(palette.RGB)))
    d = ImageDraw.Draw(img)
    for i in range(6):
        d.rectangle((i * 100, 60, i * 100 + 99, 300), fill=i)
        d.text((i * 100 + 10, 310), palette.NAMES[i], fill=0)
    d.rectangle((0, 0, 599, 399), outline=0, width=2)
    d.polygon([(10, 10), (50, 10), (10, 50)], fill=3)  # red triangle: top-left as you hold the camera
    d.text((60, 20), "TOP LEFT  -  Inkstant test chart", fill=0)
    d.text((10, 370), time.strftime("%Y-%m-%d %H:%M:%S"), fill=0)
    arr = np.asarray(img.transpose(Image.Transpose.ROTATE_90), dtype=np.uint8)
    return pipeline.pack_indices(arr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--epd", help="an .epd file to show instead of the chart")
    ap.add_argument("--white", action="store_true")
    ap.add_argument("--abort-after", type=float, help="abort the update after this many seconds")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    c.require_root()
    c.require_app_stopped()

    from inkstant.hw import epd as epd_mod
    from inkstant.hw import gpio
    from inkstant.imaging import epdfile

    pins = gpio.Pins()
    print(f"Module detect: {'module IN' if pins.module_present() else 'no module'}; "
          f"BUSY is {'high (idle)' if pins.busy.value else 'low'}")
    driver = epd_mod.EpdDriver(pins, log=print)
    result: dict = {"module_detect": pins.module_present()}

    if a.probe:
        t0 = time.monotonic()
        ok = driver.probe()
        result.update(probe=ok, probe_s=round(time.monotonic() - t0, 2))
        print(f"Probe: {'a module answered' if ok else 'no module answered'} in {result['probe_s']} s")
        driver.close()
        pins.close()
        c.save("epd_show", result)
        return 0 if ok else 1

    if a.epd:
        frame, pv = epdfile.read(a.epd)
        print(f"Showing {a.epd} (palette version {pv})")
    elif a.white:
        frame = epdfile.solid(epdfile.WHITE_CODE)
    else:
        frame = chart_frame()
    frame = epdfile.oriented(frame, c.device().display_rotation)

    # Time each BUSY wait (reset, init, power on, refresh, power off) without changing the driver.
    waits: list[float] = []
    original = driver._wait_busy

    def timed_wait(timeout, exc):
        t0 = time.monotonic()
        try:
            original(timeout, exc)
        finally:
            waits.append(time.monotonic() - t0)

    driver._wait_busy = timed_wait
    shown_at: list[float] = []
    if a.abort_after:
        def abort():
            time.sleep(a.abort_after)
            print(f"  aborting after {a.abort_after} s (pull the module out now if testing pull-out)")
            driver.request_abort()
        threading.Thread(target=abort, daemon=True).start()

    t0 = time.monotonic()
    outcome = "shown"
    try:
        driver.show(frame, on_shown=lambda: shown_at.append(time.monotonic() - t0))
    except epd_mod.ModuleMissing as e:
        outcome = f"module missing ({e})"
    except epd_mod.DisplayTimeout as e:
        outcome = f"timeout ({e})"
    except epd_mod.Aborted:
        outcome = "aborted"
    total = time.monotonic() - t0
    result.update(outcome=outcome, total_s=round(total, 2), shown_s=round(shown_at[0], 2) if shown_at else None,
                  busy_waits_s={WAITS[i] if i < len(WAITS) else str(i): round(w, 3) for i, w in enumerate(waits)})
    print(f"Outcome: {outcome}; total {total:.1f} s" + (f", image shown at {shown_at[0]:.1f} s" if shown_at else ""))
    for name, w in result["busy_waits_s"].items():
        print(f"  BUSY wait {name:9s} {w:6.2f} s (includes Waveshare's 200 ms)")
    print(f"PWR is now {'ON (wrong!)' if pins.pwr.value else 'off'}; BUSY {'high' if pins.busy.value else 'low'}")
    result["pwr_after"] = bool(pins.pwr.value)
    driver.close()
    pins.close()
    c.save("epd_show", result)
    return 0 if outcome == "shown" and not result["pwr_after"] else 1


if __name__ == "__main__":
    sys.exit(main())
