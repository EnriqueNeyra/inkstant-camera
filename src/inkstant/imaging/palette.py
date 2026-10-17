"""The panel's six colours.

Indices 0 to 5 are dither palette entries; CODES are what the panel understands
(Waveshare epd3in6e: black 0, white 1, yellow 2, red 3, blue 5, green 6).
Bump PALETTE_VERSION whenever RGB or ENHANCE change: older .epd files are rebuilt.
"""

from __future__ import annotations

PALETTE_VERSION = 4  # 4: measured on our panel; the photo is mapped into its range before dithering

NAMES = ("black", "white", "yellow", "red", "blue", "green")
CODES = (0, 1, 2, 3, 5, 6)

# Measured on our panel: the median of each swatch in a phone photo of tools/palette_tuner.py's
# chart, indoors. Black, red, yellow and blue fall within published Spectra 6 measurements
# (epdoptimize, esp32-photoframe); our green is lighter than theirs.
RGB = (
    (24, 21, 38),
    (212, 223, 225),
    (229, 214, 67),
    (114, 33, 29),
    (48, 92, 170),
    (85, 112, 103),
)

# Pimoroni's measured values for its Spectra 6 panel (inky_e640.py), used before version 3.
PIMORONI = ((0, 0, 0), (161, 164, 165), (208, 190, 71), (156, 72, 75), (61, 59, 94), (58, 91, 70))

# The panel as a phone screen should show it (the gallery's print, the simulator): our colours,
# scaled so the panel's white looks like paper.
DISPLAY_RGB = {code: tuple(min(255, round(c * 235 / w)) for c, w in zip(rgb, RGB[1], strict=True))
               for code, rgb in zip(CODES, RGB, strict=True)}

# Saturation and contrast lift before dithering, tuned with the palette.
ENHANCE = {"color": 1.2, "contrast": 1.1}


def lut():
    """256-entry index → code table. Padding indices map to black, never an undefined code."""
    import numpy as np

    table = np.zeros(256, dtype=np.uint8)
    table[: len(CODES)] = CODES
    return table


def palette_image(rgb=RGB):
    """A `P` image holding the six colours at 0..5; the rest repeat black, as Waveshare pads.
    The padding is this palette's black: a plain (0, 0, 0) would be nearer to dark pixels than a
    measured black, and Pillow would dither against a colour the panel doesn't have."""
    from PIL import Image

    flat = []
    for c in rgb:
        flat.extend(c)
    flat.extend(rgb[0] * (256 - len(rgb)))
    img = Image.new("P", (1, 1))
    img.putpalette(flat)
    return img
