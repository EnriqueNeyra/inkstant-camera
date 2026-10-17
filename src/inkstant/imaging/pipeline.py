"""Camera frame → screen image, thumbnail and EXIF.

The only code that imports numpy and Pillow; it runs in the capture worker.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

from inkstant import version
from inkstant.imaging import epdfile, palette

LORES_W, LORES_H = 1152, 648
CROP_X = 90  # 16:9 → 3:2 drops 90 px from each side of the lores frame
SCREEN_W, SCREEN_H = 600, 400
THUMB_W, THUMB_H = 480, 270
THUMB_QUALITY = 80
JPEG_QUALITY = 90


# Lores YUV420 → RGB ---------------------------------------------------------
def split_yuv420(arr: np.ndarray, width: int = LORES_W, height: int = LORES_H):
    """Y, U, V planes from Picamera2's YUV420 array of shape (height * 3 / 2, stride).

    The same reshape Picamera2 uses in `request.save`, so any row padding is handled.
    """
    if arr.ndim != 2 or arr.shape[0] != height * 3 // 2:
        raise ValueError(f"unexpected YUV420 array shape {arr.shape}")
    y = arr[:height, :width]
    stride = arr.strides[0]
    if not arr.flags["C_CONTIGUOUS"]:
        arr = np.ascontiguousarray(arr)
        stride = arr.strides[0]
    reshaped = arr.reshape((arr.shape[0] * 2, stride // 2))
    u = reshaped[2 * height : 2 * height + height // 2, : width // 2]
    v = reshaped[2 * height + height // 2 :, : width // 2]
    return y, u, v


def yuv_to_rgb(y: np.ndarray, u: np.ndarray, v: np.ndarray, crop: bool) -> Image.Image:
    """Full-range BT.601 YCbCr (the camera's Sycc stills colour space) to an RGB image."""
    if crop:
        h, w = y.shape
        y = y[:, CROP_X : w - CROP_X]
        u = u[:, CROP_X // 2 : (w - CROP_X) // 2]
        v = v[:, CROP_X // 2 : (w - CROP_X) // 2]
    yi = Image.fromarray(np.ascontiguousarray(y), "L")
    size = yi.size
    ui = Image.fromarray(np.ascontiguousarray(u), "L").resize(size, Image.Resampling.BILINEAR)
    vi = Image.fromarray(np.ascontiguousarray(v), "L").resize(size, Image.Resampling.BILINEAR)
    return Image.merge("YCbCr", (yi, ui, vi)).convert("RGB")


def crop_3_2(img: Image.Image) -> Image.Image:
    """Centre-crop any image to 3:2 (for rebuilding from a JPEG)."""
    w, h = img.size
    target_w = round(h * 3 / 2)
    if target_w <= w:
        left = (w - target_w) // 2
        return img.crop((left, 0, left + target_w, h))
    target_h = round(w * 2 / 3)
    top = (h - target_h) // 2
    return img.crop((0, top, w, top + target_h))


# Screen image -------------------------------------------------------------------
def to_panel_range(img: Image.Image, rgb=palette.RGB) -> Image.Image:
    """Map 0..255 onto the panel's black..white. Its white is a light grey, so without this
    everything brighter than that grey dithers to solid white and the highlights blow out."""
    lut: list[int] = []
    for black, white in zip(rgb[0], rgb[1], strict=True):
        lut.extend(round(black + v * (white - black) / 255) for v in range(256))
    return img.point(lut)


def screen_frame(rgb_3_2: Image.Image) -> bytes:
    """A 3:2 RGB image → the panel's 120,000-byte frame (crop, resize, lift, dither, pack)."""
    img = rgb_3_2.resize((SCREEN_W, SCREEN_H), Image.Resampling.LANCZOS)
    img = ImageEnhance.Color(img).enhance(palette.ENHANCE["color"])
    img = ImageEnhance.Contrast(img).enhance(palette.ENHANCE["contrast"])
    img = to_panel_range(img)
    img = img.transpose(Image.Transpose.ROTATE_90)  # 400 × 600, as Waveshare's getbuffer does
    indices = img.quantize(palette=palette.palette_image(), dither=Image.Dither.FLOYDSTEINBERG)
    return pack_indices(np.asarray(indices, dtype=np.uint8))


def pack_indices(indices: np.ndarray) -> bytes:
    """Palette indices (600 rows × 400) → panel codes, two pixels per byte."""
    if indices.shape != (epdfile.HEIGHT, epdfile.WIDTH):
        raise ValueError(f"expected {epdfile.HEIGHT}x{epdfile.WIDTH} indices, got {indices.shape}")
    codes = palette.lut()[indices].ravel()
    return ((codes[0::2] << 4) | codes[1::2]).astype(np.uint8).tobytes()


def bw_frame(img_600x400: Image.Image) -> bytes:
    """A black-and-white 600 × 400 image (the share screen) → frame, with no dithering."""
    gray = img_600x400.convert("L").transpose(Image.Transpose.ROTATE_90)
    arr = np.asarray(gray)
    codes = np.where(arr >= 128, epdfile.WHITE_CODE, 0).astype(np.uint8).ravel()
    return ((codes[0::2] << 4) | codes[1::2]).astype(np.uint8).tobytes()


def _codes(frame: bytes) -> np.ndarray:
    raw = np.frombuffer(frame, dtype=np.uint8)
    codes = np.empty(raw.size * 2, dtype=np.uint8)
    codes[0::2] = raw >> 4
    codes[1::2] = raw & 0x0F
    return codes


def print_png(epd: bytes) -> bytes:
    """The print as the panel shows it, for the gallery: a 600 × 400 PNG in the six colours."""
    frame, _ = epdfile.unpack(epd)
    img = Image.fromarray(_codes(frame).reshape(epdfile.HEIGHT, epdfile.WIDTH), "P")
    flat = [0] * 48
    for code, rgb in palette.DISPLAY_RGB.items():
        flat[code * 3 : code * 3 + 3] = rgb
    img.putpalette(flat)
    buf = io.BytesIO()
    img.transpose(Image.Transpose.ROTATE_270).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def frame_to_image(frame: bytes, rotation: int = 0) -> Image.Image:
    """Decode a frame back to a 600 × 400 landscape picture (simulator, tests)."""
    codes = _codes(frame)
    lut = np.zeros((16, 3), dtype=np.uint8)
    for code, rgb in palette.DISPLAY_RGB.items():
        lut[code] = rgb
    img = Image.fromarray(lut[codes].reshape(epdfile.HEIGHT, epdfile.WIDTH, 3), "RGB")
    img = img.transpose(Image.Transpose.ROTATE_270)  # back to landscape
    if rotation == 180:
        img = img.transpose(Image.Transpose.ROTATE_180)
    return img


# Thumbnail and EXIF ----------------------------------------------------------------
def thumbnail_jpeg(rgb_16_9: Image.Image) -> bytes:
    img = rgb_16_9.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return buf.getvalue()


def exif_dict(taken: datetime | None, metadata: dict | None) -> dict:
    """Our EXIF IFDs, passed whole so they replace Picamera2's. piexif's format."""
    import piexif

    zeroth = {
        piexif.ImageIFD.Make: b"Inkstant",
        piexif.ImageIFD.Model: b"Inkstant Camera",
        piexif.ImageIFD.Software: f"inkstant {version.VERSION}".encode(),
    }
    exif: dict = {}
    if taken is not None:
        stamp = taken.strftime("%Y:%m:%d %H:%M:%S").encode()
        zeroth[piexif.ImageIFD.DateTime] = stamp
        exif[piexif.ExifIFD.DateTimeOriginal] = stamp
    md = metadata or {}
    if isinstance(md.get("ExposureTime"), int) and md["ExposureTime"] > 0:
        exif[piexif.ExifIFD.ExposureTime] = (int(md["ExposureTime"]), 1_000_000)
    if "AnalogueGain" in md and "DigitalGain" in md:
        exif[piexif.ExifIFD.ISOSpeedRatings] = int(md["AnalogueGain"] * md["DigitalGain"] * 100)
    return {"0th": zeroth, "Exif": exif}


from inkstant.imaging.exifinfo import exif_info  # noqa: E402,F401  (kept here for callers)


# Whole jobs -------------------------------------------------------------------------
def from_lores(lores: np.ndarray) -> tuple[bytes, bytes]:
    """Lores YUV420 → (.epd file bytes, thumbnail JPEG bytes)."""
    y, u, v = split_yuv420(lores)
    epd = epdfile.pack(screen_frame(yuv_to_rgb(y, u, v, crop=True)), palette.PALETTE_VERSION)
    thumb = thumbnail_jpeg(yuv_to_rgb(y, u, v, crop=False))
    return epd, thumb


def from_jpeg(jpeg_path: Path) -> tuple[bytes, bytes]:
    """Rebuild the screen image and thumbnail from a saved photo (after a power cut, or a palette change)."""
    with Image.open(jpeg_path) as im:
        im.draft("RGB", (LORES_W, LORES_H))  # libjpeg's quarter-size decode of 4608 × 2592
        rgb = im.convert("RGB")
    if rgb.size != (LORES_W, LORES_H):
        rgb = rgb.resize((LORES_W, LORES_H), Image.Resampling.LANCZOS)
    w, h = rgb.size
    screen = rgb.crop((CROP_X, 0, w - CROP_X, h))
    epd = epdfile.pack(screen_frame(screen), palette.PALETTE_VERSION)
    return epd, thumbnail_jpeg(rgb)


def encode_jpeg(rgb: Image.Image, exif: dict | None = None, quality: int = JPEG_QUALITY) -> bytes:
    """Pillow JPEG encode (the simulator's camera; the Pi uses Picamera2's YUV encoder)."""
    buf = io.BytesIO()
    kwargs = {}
    if exif:
        import piexif

        kwargs["exif"] = piexif.dump(exif)
    rgb.save(buf, format="JPEG", quality=quality, **kwargs)
    return buf.getvalue()


def rgb_to_yuv420(rgb: Image.Image) -> np.ndarray:
    """RGB → a Picamera2-style YUV420 array (fake camera and tests)."""
    ycc = np.asarray(rgb.convert("YCbCr"))
    h, w = ycc.shape[:2]
    y = ycc[:, :, 0]
    u = np.asarray(Image.fromarray(ycc[:, :, 1], "L").resize((w // 2, h // 2), Image.Resampling.BILINEAR))
    v = np.asarray(Image.fromarray(ycc[:, :, 2], "L").resize((w // 2, h // 2), Image.Resampling.BILINEAR))
    # Planes packed as Picamera2 lays them out: U and V rows are half the stride.
    out = np.empty((h * 3 // 2, w), dtype=np.uint8)
    out[:h] = y
    half = out[h:].reshape(h, w // 2)  # view onto the chroma rows, half-stride
    half[: h // 2] = u
    half[h // 2 :] = v
    return out
