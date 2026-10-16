#!/usr/bin/env python3
"""Full-resolution capture and camera memory (SPEC.md §19.5, §19.6 "Camera").

Starts the camera exactly as the app does (§6.4: 4608 × 2592 main + 1152 × 648 lores, YUV420,
2 buffers, tuning override), logs CmaFree before and after, takes 20 photos through the app's
own capture path, and times the screen image, thumbnail and JPEG against §7.6's targets.

    sudo systemctl stop inkstant
    sudo python3 tools/bench/camera_fullres.py [--frames 20] [--fps 5]
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import _common as c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--fps", type=int, default=None, help="running frame rate (default: the app's)")
    a = ap.parse_args()
    c.require_root()
    c.require_app_stopped()

    from inkstant.hw import camera as cam_mod
    from inkstant.imaging import pipeline

    result: dict = {"frames": a.frames}
    mem0 = c.meminfo()
    result["cma_total_kb"] = mem0.get("CmaTotal")
    result["cma_free_before_kb"] = mem0.get("CmaFree")
    print(f"CmaTotal {mem0.get('CmaTotal')} kB, CmaFree {mem0.get('CmaFree')} kB before the camera starts")

    t = time.monotonic()
    import picamera2  # noqa: F401  (sets LIBCAMERA_RPI_CONFIG_FILE)

    result["picamera2_import_s"] = round(time.monotonic() - t, 2)
    cfg = os.environ.get("LIBCAMERA_RPI_CONFIG_FILE")
    result["libcamera_config_file"] = cfg
    print(f"Picamera2 import: {result['picamera2_import_s']} s; LIBCAMERA_RPI_CONFIG_FILE = {cfg}")
    if not cfg or "rpi_apps.yaml" not in cfg:
        print("  WARNING: libcamera isn't using rpi_apps.yaml, so the camera needs ~117 MB of CMA instead of ~69 MB")

    cam = cam_mod.Camera(c.device(), log=print, fps=a.fps)
    t = time.monotonic()
    try:
        mode = cam.open()
    except Exception as e:
        print(f"FAIL: the camera didn't start: {e}")
        result["error"] = str(e)
        c.save("camera_fullres", result)
        return 1
    result["open_s"] = round(time.monotonic() - t, 2)
    result["mode"] = mode
    mem1 = c.meminfo()
    result["cma_free_running_kb"] = mem1.get("CmaFree")
    used = (mem0.get("CmaFree", 0) - mem1.get("CmaFree", 0)) // 1024
    print(f"Camera started in {result['open_s']} s, mode {mode!r}; CmaFree {mem1.get('CmaFree')} kB "
          f"(camera uses about {used} MB)")

    shots, lores_ms, jpeg_ms, capture_ms = [], [], [], []
    errors = []
    first_jpeg = None
    for i in range(a.frames):
        made: dict = {}

        def screen(lores, md, made=made):
            t0 = time.monotonic()
            made["epd"], made["thumb"] = pipeline.from_lores(lores)
            made["ms"] = (time.monotonic() - t0) * 1000

        def on_lores(lores, md, made=made):
            made["shape"] = lores.shape
            made["thread"] = threading.Thread(target=screen, args=(lores, md))
            made["thread"].start()
            made["t_lores"] = time.monotonic()

        t0 = time.monotonic()
        try:
            shot = cam.capture(time.monotonic_ns(), lambda md: pipeline.exif_dict(None, md), on_lores)
        except Exception as e:
            errors.append(f"frame {i + 1}: {e!r}")
            print(f"  frame {i + 1}: FAILED {e!r}")
            continue
        t1 = time.monotonic()
        made["thread"].join()
        capture_ms.append((made["t_lores"] - t0) * 1000)
        jpeg_ms.append((t1 - made["t_lores"]) * 1000)
        lores_ms.append(made["ms"])
        md = shot.metadata
        shots.append({"exposure_us": md.get("ExposureTime"), "gain": md.get("AnalogueGain"),
                      "lens": md.get("LensPosition"), "af_state": md.get("AfState"),
                      "jpeg_bytes": len(shot.jpeg), "frame_duration_us": md.get("FrameDuration")})
        if first_jpeg is None:
            first_jpeg = shot.jpeg
        print(f"  frame {i + 1}: frame in hand {capture_ms[-1]:.0f} ms, JPEG {jpeg_ms[-1]:.0f} ms "
              f"({len(shot.jpeg) // 1024} KB), screen image + thumbnail {lores_ms[-1]:.0f} ms")
    mem2 = c.meminfo()
    result["cma_free_after_kb"] = mem2.get("CmaFree")

    # Sleep and wake (§8.8): no new CMA allocation on waking.
    try:
        cam.sleep()
        asleep = c.meminfo().get("CmaFree")
        time.sleep(1)
        t0 = time.monotonic()
        cam.wake(for_shot=True)
        result["wake_for_shot_ms"] = round((time.monotonic() - t0) * 1000)
        awake = c.meminfo().get("CmaFree")
        result["cma_free_asleep_kb"], result["cma_free_woken_kb"] = asleep, awake
        print(f"Sleep/wake: woke and focused in {result['wake_for_shot_ms']} ms; CmaFree asleep {asleep} kB, "
              f"after waking {awake} kB")
    except Exception as e:
        errors.append(f"sleep/wake: {e!r}")
    cam.close()
    result["cma_free_closed_kb"] = c.meminfo().get("CmaFree")

    result.update(shots=shots, errors=errors, frame_in_hand_ms=c.stats(capture_ms), jpeg_encode_ms=c.stats(jpeg_ms),
                  screen_and_thumb_ms=c.stats(lores_ms))
    if first_jpeg:
        p = c.results_dir() / "camera_fullres-first.jpg"
        p.write_bytes(first_jpeg)
        print(f"First photo saved to {p} (check it's sharp and the right way up)")
    print()
    print("Frame in hand:          " + c.fmt_stats(result["frame_in_hand_ms"]))
    print("Full JPEG encode:       " + c.fmt_stats(result["jpeg_encode_ms"]) + "   target ≤ 2000 ms")
    print("Screen image + thumb:   " + c.fmt_stats(result["screen_and_thumb_ms"]) + "   target ≤ 900 ms")
    ok = not errors and mode == "full" and len(shots) == a.frames
    print("PASS" if ok else "FAIL: " + "; ".join(errors or [f"mode {mode}"]))
    c.save("camera_fullres", result)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
