#!/usr/bin/env python3
"""Tune the dithering palette on the real panel (SPEC.md §7.7).

Shows calibration screens: the six pure colours with ramps, or a reference photo dithered
with two candidate palettes side by side (left half A, right half B). Pick the one that looks
closest to the photo, then put its colours in src/inkstant/imaging/palette.py and raise
PALETTE_VERSION by one; the camera rebuilds every screen image on its next start.

    sudo systemctl stop inkstant
    sudo python3 tools/palette_tuner.py chart
    sudo python3 tools/palette_tuner.py photo ref.jpg --a pimoroni --b mid
    sudo python3 tools/palette_tuner.py photo ref.jpg --a pimoroni --b "0,0,0 170,170,170 215,195,70 160,70,70 60,60,100 60,95,70"
    python3 tools/palette_tuner.py photo ref.jpg --png preview.png     # no panel: write a preview

Candidates: pimoroni (the current palette), pure (Waveshare's), mid (halfway between them), or
six "r,g,b" triples in the order black, white, yellow, red, blue, green.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "bench"))  # _common, for the panel

PURE = ((0, 0, 0), (255, 255, 255), (255, 255, 0), (255, 0, 0), (0, 0, 255), (0, 255, 0))


def candidate(spec: str) -> tuple[tuple[int, int, int], ...]:
    from inkstant.imaging import palette

    if spec == "pimoroni":
        return palette.RGB
    if spec == "pure":
        return PURE
    if spec == "mid":
        return tuple(tuple((a + b) // 2 for a, b in zip(p, q, strict=True)) for p, q in zip(palette.RGB, PURE, strict=True))
    triples = spec.split()
    if len(triples) != 6:
        raise SystemExit(f"palette {spec!r}: give a name or six r,g,b triples")
    out = []
    for t in triples:
        r, g, b = (int(x) for x in t.split(","))
        out.append((r, g, b))
    return tuple(out)


def pal_image(rgb):
    from PIL import Image

    flat = [v for c in rgb for v in c] + [0, 0, 0] * (256 - len(rgb))
    img = Image.new("P", (1, 1))
    img.putpalette(flat)
    return img


def dither(img, rgb, color: float, contrast: float):
    """A 600 × 400 RGB image → a 600 × 400 `P` image of indices 0..5."""
    from PIL import Image, ImageEnhance

    img = ImageEnhance.Color(img.convert("RGB")).enhance(color)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    return img.quantize(palette=pal_image(rgb), dither=Image.Dither.FLOYDSTEINBERG)


def chart(rgb, color: float, contrast: float):
    """Six swatches, then ramps from black to white through each colour, dithered."""
    from PIL import Image, ImageDraw

    from inkstant.imaging import palette

    swatches = Image.new("P", (600, 100), 1)
    swatches.putpalette(pal_image(rgb).getpalette())
    d = ImageDraw.Draw(swatches)
    for i in range(6):
        d.rectangle((i * 100, 0, i * 100 + 99, 79), fill=i)
        d.text((i * 100 + 8, 84), palette.NAMES[i], fill=0)
    ramps = Image.new("RGB", (600, 300), (255, 255, 255))
    targets = [(255, 255, 255), (255, 220, 0), (220, 30, 30), (30, 60, 200), (30, 150, 60), (230, 180, 150)]
    for row, target in enumerate(targets):
        for x in range(600):
            f = x / 599
            if f < 0.5:  # black → colour
                col = tuple(int(t * f * 2) for t in target)
            else:  # colour → white
                col = tuple(int(t + (255 - t) * (f - 0.5) * 2) for t in target)
            for y in range(row * 50, row * 50 + 48):
                ramps.putpixel((x, y), col)
    out = Image.new("P", (600, 400), 1)
    out.putpalette(pal_image(rgb).getpalette())
    out.paste(swatches, (0, 0))
    out.paste(dither(ramps, rgb, color, contrast), (0, 100))
    return out


def photo(path: str, a_rgb, b_rgb, color: float, contrast: float):
    from PIL import Image, ImageDraw, ImageOps

    from inkstant.imaging import pipeline

    with Image.open(path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    img = pipeline.crop_3_2(img).resize((600, 400), Image.Resampling.LANCZOS)
    left = dither(img, a_rgb, color, contrast)
    right = dither(img, b_rgb, color, contrast)
    out = left.copy()
    out.paste(right.crop((300, 0, 600, 400)), (300, 0))
    d = ImageDraw.Draw(out)
    d.line((300, 0, 300, 399), fill=1, width=1)
    d.text((6, 4), "A", fill=1)
    d.text((588, 4), "B", fill=1)
    return out


def to_frame(p_img) -> bytes:
    import numpy as np
    from PIL import Image

    from inkstant.imaging import pipeline

    arr = np.asarray(p_img.transpose(Image.Transpose.ROTATE_90), dtype=np.uint8)
    return pipeline.pack_indices(arr)


def show(frame: bytes) -> None:
    import _common as c

    c.require_root()
    c.require_app_stopped()
    from inkstant.hw import epd as epd_mod
    from inkstant.hw import gpio
    from inkstant.imaging import epdfile

    pins = gpio.Pins()
    driver = epd_mod.EpdDriver(pins, log=print)
    try:
        driver.show(epdfile.oriented(frame, c.device().display_rotation))
        print("Shown.")
    finally:
        driver.close()
        pins.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ch = sub.add_parser("chart")
    ch.add_argument("--palette", default="pimoroni")
    ph = sub.add_parser("photo")
    ph.add_argument("image")
    ph.add_argument("--a", default="pimoroni")
    ph.add_argument("--b", default="mid")
    for p in (ch, ph):
        p.add_argument("--color", type=float, default=None, help="saturation lift (default: palette.ENHANCE)")
        p.add_argument("--contrast", type=float, default=None)
        p.add_argument("--png", help="write a preview PNG instead of using the panel")
    a = ap.parse_args()

    from inkstant.imaging import palette, pipeline

    color = a.color if a.color is not None else palette.ENHANCE["color"]
    contrast = a.contrast if a.contrast is not None else palette.ENHANCE["contrast"]
    if a.cmd == "chart":
        rgb = candidate(a.palette)
        img = chart(rgb, color, contrast)
        print(f"Chart dithered with {a.palette}: {rgb}")
    else:
        a_rgb, b_rgb = candidate(a.a), candidate(a.b)
        img = photo(a.image, a_rgb, b_rgb, color, contrast)
        print(f"A (left)  = {a.a}: {a_rgb}\nB (right) = {a.b}: {b_rgb}")
    print(f"Enhance: color {color}, contrast {contrast}")
    frame = to_frame(img)
    if a.png:
        pipeline.frame_to_image(frame).save(a.png)
        print(f"Preview written to {a.png} (as the panel's pure colours, roughly)")
        return 0
    show(frame)
    print("\nTo adopt a palette: set RGB (and ENHANCE) in src/inkstant/imaging/palette.py and raise "
          "PALETTE_VERSION by one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
