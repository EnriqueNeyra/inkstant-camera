"""The share screen: QR codes and text, in pure black and white."""

from __future__ import annotations

from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

from inkstant.imaging import epdfile, pipeline

W, H = pipeline.SCREEN_W, pipeline.SCREEN_H
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
BLACK, WHITE = 0, 255


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for path in (FONT_DIR / name, Path(__file__).parent / name):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def wifi_qr_text(ssid: str, password: str) -> str:
    """The Wi-Fi join code phones understand (ZXing format), with \\ ; , : " escaped."""

    def esc(s: str) -> str:
        out = []
        for ch in s:
            if ch in '\\;,:"':
                out.append("\\")
            out.append(ch)
        return "".join(out)

    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};H:false;;"


def qr_matrix(text: str) -> list[list[bool]]:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix()


def draw_qr(img: Image.Image, text: str, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Draw a QR code as whole pixels per module at the largest scale that fits `box`."""
    matrix = qr_matrix(text)
    n = len(matrix)
    x0, y0, x1, y1 = box
    scale = max(1, min((x1 - x0) // n, (y1 - y0) // n))
    size = n * scale
    left = x0 + ((x1 - x0) - size) // 2
    top = y0 + ((y1 - y0) - size) // 2
    draw = ImageDraw.Draw(img)
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                draw.rectangle((left + c * scale, top + r * scale,
                                left + (c + 1) * scale - 1, top + (r + 1) * scale - 1), fill=BLACK)
    return left, top, left + size, top + size


def _centered(draw: ImageDraw.ImageDraw, text: str, cx: int, y: int, font, fill=BLACK) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    w = box[2] - box[0]
    draw.text((cx - w // 2, y), text, font=font, fill=fill)
    return y + (box[3] - box[1])


def _fit(draw, text: str, max_w: int, size: int, bold: bool = False):
    """The largest font at or under `size` that fits `text` in `max_w` pixels."""
    while size > 10:
        font = _font(size, bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_w:
            return font
        size -= 1
    return _font(10, bold)


def render_away(ssid: str, password: str, url: str = "http://10.42.0.1/", android_hint: bool = True,
                home_hint: bool = False) -> Image.Image:
    """`home_hint`: the camera's Wi-Fi was chosen by a double tap at home, so say how to go back."""
    img = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(img)
    head = _font(22, bold=True)
    _centered(draw, "1. Join Wi-Fi", 150, 10, head)
    _centered(draw, "2. Open photos", 450, 10, head)
    draw_qr(img, wifi_qr_text(ssid, password), (40, 40, 260, 260))
    draw_qr(img, url, (340, 40, 560, 260))
    y = 272
    gap = 6 if home_hint else 10  # room for one more line
    for label, value in (("Wi-Fi", ssid), ("Password", password), ("Address", url.rstrip("/"))):
        text = f"{label}:  {value}"
        y = _centered(draw, text, W // 2, y, _fit(draw, text, W - 40, 21)) + gap
    if android_hint:
        y = _centered(draw, "If your phone asks, choose to stay connected.", W // 2, y + 2, _font(15)) + 5
    if home_hint:
        _centered(draw, "Double-tap the share button to go back to home Wi-Fi.", W // 2, y, _font(15))
    return img


def render_home(url: str, network: str) -> Image.Image:
    img = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(img)
    left, top, right, bottom = draw_qr(img, url, (10, 10, 290, 390))
    cx = (right + W) // 2
    y = 110
    y = _centered(draw, "Open photos", cx, y, _font(24, bold=True)) + 18
    addr = url.rstrip("/").removeprefix("http://")
    y = _centered(draw, addr, cx, y, _fit(draw, addr, W - right - 20, 22, bold=True)) + 14
    on = f"On {network}"
    y = _centered(draw, on, cx, y, _fit(draw, on, W - right - 20, 18)) + 40
    small = _font(14)
    for line in ("Not on this Wi-Fi? Double-tap", "the share button for the", "camera's own."):
        y = _centered(draw, line, cx, y, small) + 4
    return img


def frame_away(ssid: str, password: str, url: str = "http://10.42.0.1/", home_hint: bool = False) -> bytes:
    return pipeline.bw_frame(render_away(ssid, password, url, home_hint=home_hint))


def frame_home(url: str, network: str) -> bytes:
    return pipeline.bw_frame(render_home(url, network))


assert epdfile.FRAME_BYTES == W * H // 2
