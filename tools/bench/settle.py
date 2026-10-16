#!/usr/bin/env python3
"""Focus, exposure and colour settling (SPEC.md §19.5, §6.4 tuning override).

Logs every frame's lens position, AfState, exposure, gain and colour gains after you re-aim
from far to near, and after you switch a lamp on or off. Run it with and without the tuning
override at each frame rate, then compare the settle times.

    sudo systemctl stop inkstant
    sudo python3 tools/bench/settle.py --fps 5 10 14
    sudo python3 tools/bench/settle.py --fps 5 --no-override
"""

from __future__ import annotations

import argparse
import sys
import time

import _common as c

RECORD_S = 4.0
TOLERANCE = {"LensPosition": 0.05, "ExposureTime": 0.10, "AnalogueGain": 0.10, "ColourGains": 0.05}


def settle_ms(frames: list[dict], key: str) -> float | None:
    """Time until `key` stays within its tolerance of the final value for good."""
    vals = [f.get(key) for f in frames]
    if not vals or vals[-1] is None:
        return None

    def num(v):
        return v[0] / v[1] if isinstance(v, (list, tuple)) and len(v) == 2 and v[1] else v

    final = num(vals[-1])
    tol = TOLERANCE[key] * max(abs(final), 1e-6)
    last_bad = None
    for f, v in zip(frames, vals, strict=True):
        if v is None or abs(num(v) - final) > tol:
            last_bad = f["t_ms"]
    return 0.0 if last_bad is None else last_bad


def record(cam, label: str) -> list[dict]:
    frames = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < RECORD_S:
        md = cam.picam2.capture_metadata(wait=2)
        frames.append({"t_ms": round((time.monotonic() - t0) * 1000), "LensPosition": md.get("LensPosition"),
                       "AfState": int(md["AfState"]) if "AfState" in md else None,
                       "ExposureTime": md.get("ExposureTime"), "AnalogueGain": md.get("AnalogueGain"),
                       "ColourGains": list(md["ColourGains"]) if "ColourGains" in md else None,
                       "Lux": md.get("Lux")})
    return frames


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fps", type=int, nargs="+", default=[5, 10, 14])
    ap.add_argument("--no-override", action="store_true", help="libcamera's stock tuning, for comparison")
    a = ap.parse_args()
    c.require_root()
    c.require_app_stopped()

    from inkstant.hw import camera as cam_mod

    class StockTuning(cam_mod.Camera):
        def _tuning(self, model):
            return None

    cls = StockTuning if a.no_override else cam_mod.Camera
    cam = cls(c.device(), log=print)
    cam.open()
    result: dict = {"override": not a.no_override, "rates": {}}
    tests = [
        ("refocus", "Aim at something far away (3 m or more) and hold still.",
         "Now re-aim at something near (30 to 50 cm) as soon as you press Enter.", ["LensPosition"]),
        ("light", "Aim at a still scene lit by a lamp you can reach.",
         "Switch the lamp on or off as soon as you press Enter.",
         ["ExposureTime", "AnalogueGain", "ColourGains"]),
    ]
    for fps in sorted(a.fps):
        cam.set_fps(fps)
        result["rates"][fps] = {}
        print(f"\n=== {fps} fps, tuning override {'off' if a.no_override else 'on'} ===")
        for name, before, action, keys in tests:
            c.ask(f"{before} Press Enter when ready. ")
            time.sleep(1.5)
            c.ask(f"{action} ")
            frames = record(cam, name)
            settled = {k: settle_ms(frames, k) for k in keys}
            result["rates"][fps][name] = {"frames": frames, "settle_ms": settled}
            print("  settled after: " + ", ".join(f"{k} {v:.0f} ms" if v is not None else f"{k} n/a"
                                               for k, v in settled.items()))
    cam.close()
    print("\nSummary (settle times include your reaction time; compare runs, not absolutes)")
    for fps, r in result["rates"].items():
        line = f"  {fps:2d} fps: "
        line += "; ".join(f"{n}: " + ", ".join(f"{k} {v:.0f} ms" for k, v in t["settle_ms"].items() if v is not None)
                          for n, t in r.items())
        print(line)
    c.save("settle-" + ("stock" if a.no_override else "override"), result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
