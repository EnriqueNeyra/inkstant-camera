"""The .epd screen-image file (SPEC.md §7.2). Standard library only: the slideshow wake loads it.

16-byte header + 120,000-byte frame. The frame is the panel's native 400 × 600 portrait
order, two pixels per byte, first pixel in the high nibble, panel colour codes.
"""

from __future__ import annotations

import struct
from pathlib import Path

MAGIC = b"IKEP"
FORMAT_VERSION = 1
WIDTH = 400
HEIGHT = 600
FRAME_BYTES = WIDTH * HEIGHT // 2  # 120,000
HEADER_BYTES = 16
FILE_BYTES = HEADER_BYTES + FRAME_BYTES
_HEADER = struct.Struct("<4sBBHHHI")  # magic, version, flags, palette, width, height, reserved

WHITE_CODE = 1
_SWAP = bytes(((b & 0x0F) << 4) | (b >> 4) for b in range(256))


class EpdFileError(ValueError):
    pass


def pack(frame: bytes, palette_version: int) -> bytes:
    if len(frame) != FRAME_BYTES:
        raise EpdFileError(f"frame is {len(frame)} bytes, expected {FRAME_BYTES}")
    return _HEADER.pack(MAGIC, FORMAT_VERSION, 0, palette_version, WIDTH, HEIGHT, 0) + bytes(frame)


def unpack(data: bytes) -> tuple[bytes, int]:
    """Returns (frame, palette version). Rejects a wrong magic or size."""
    if len(data) != FILE_BYTES:
        raise EpdFileError(f"file is {len(data)} bytes, expected {FILE_BYTES}")
    magic, version, _flags, palette, width, height, _ = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise EpdFileError("not an .epd file")
    if version != FORMAT_VERSION or (width, height) != (WIDTH, HEIGHT):
        raise EpdFileError(f"unsupported .epd format {version} {width}x{height}")
    return data[HEADER_BYTES:], palette


def read(path: Path) -> tuple[bytes, int]:
    return unpack(Path(path).read_bytes())


def rotate180(frame: bytes) -> bytes:
    """Reverse the pixel order: reverse the bytes, then swap each byte's two pixels."""
    return frame[::-1].translate(_SWAP)


def oriented(frame: bytes, rotation: int) -> bytes:
    """The frame to send for a display mounted at `rotation` degrees (0 or 180)."""
    return rotate180(frame) if rotation == 180 else frame


def solid(code: int) -> bytes:
    """A frame of one colour, e.g. WHITE_CODE for the white screen."""
    return bytes([(code << 4) | code]) * FRAME_BYTES


def codes(frame: bytes) -> list[int]:
    """Every pixel's colour code, row by row (for the simulator and tests)."""
    out = []
    for b in frame:
        out.append(b >> 4)
        out.append(b & 0x0F)
    return out
