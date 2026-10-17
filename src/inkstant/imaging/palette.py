"""The panel's six colours.

Indices 0 to 5 are dither palette entries; CODES are what the panel understands
(Waveshare epd3in6e: black 0, white 1, yellow 2, red 3, blue 5, green 6).
Bump PALETTE_VERSION whenever RGB or ENHANCE change: older .epd files are rebuilt.
"""

from __future__ import annotations

PALETTE_VERSION = 1

NAMES = ("black", "white", "yellow", "red", "blue", "green")
CODES = (0, 1, 2, 3, 5, 6)

# The panel's real, muted colours: Pimoroni's measured values (inky_e640.py) until tuned on our panel.
RGB = (
    (0, 0, 0),
    (161, 164, 165),
    (208, 190, 71),
    (156, 72, 75),
    (61, 59, 94),
    (58, 91, 70),
)

# Pure colours, for decoding a frame back to an image the way the panel would show it brightly.
DISPLAY_RGB = {0: (20, 20, 20), 1: (235, 235, 230), 2: (230, 200, 40), 3: (190, 40, 40),
               5: (40, 60, 160), 6: (40, 120, 70)}

# Saturation and contrast lift before dithering, tuned with the palette.
ENHANCE = {"color": 1.2, "contrast": 1.1}


def lut():
    """256-entry index → code table. Padding indices map to black, never an undefined code."""
    import numpy as np

    table = np.zeros(256, dtype=np.uint8)
    table[: len(CODES)] = CODES
    return table


def palette_image():
    """A `P` image holding the six colours at 0..5; the rest repeat black, as Waveshare pads."""
    from PIL import Image

    flat = []
    for rgb in RGB:
        flat.extend(rgb)
    flat.extend((0, 0, 0) * (256 - len(RGB)))
    img = Image.new("P", (1, 1))
    img.putpalette(flat)
    return img
