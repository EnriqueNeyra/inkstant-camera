import numpy as np
import pytest
from PIL import Image

from inkstant.imaging import epdfile, palette, pipeline, share_screen


def gradient(w=1152, h=648):
    x = np.linspace(0, 255, w, dtype=np.uint8)
    y = np.linspace(0, 255, h, dtype=np.uint8)
    r = np.tile(x, (h, 1))
    g = np.tile(y[:, None], (1, w))
    b = 255 - r
    return Image.fromarray(np.dstack([r, g, b]), "RGB")


def test_epd_pack_unpack_and_rejects():
    frame = bytes(range(256)) * (epdfile.FRAME_BYTES // 256) + bytes(epdfile.FRAME_BYTES % 256)
    data = epdfile.pack(frame, 7)
    assert len(data) == 120_016
    assert epdfile.unpack(data) == (frame, 7)
    with pytest.raises(epdfile.EpdFileError):
        epdfile.unpack(b"XXXX" + data[4:])
    with pytest.raises(epdfile.EpdFileError):
        epdfile.unpack(data[:-1])


def slow_rotate180(frame):
    px = epdfile.codes(frame)[::-1]
    return bytes((px[i] << 4) | px[i + 1] for i in range(0, len(px), 2))


def test_rotate180_matches_slow_reference():
    rng = np.random.default_rng(1)
    frame = rng.integers(0, 256, epdfile.FRAME_BYTES, dtype=np.uint8).tobytes()
    assert epdfile.rotate180(frame) == slow_rotate180(frame)
    assert epdfile.rotate180(epdfile.rotate180(frame)) == frame
    assert epdfile.oriented(frame, 0) == frame


def waveshare_pack(indices_flat):
    """Waveshare getbuffer's packing loop, on the same indices."""
    buf = [0] * (len(indices_flat) // 2)
    for idx, i in enumerate(range(0, len(indices_flat), 2)):
        buf[idx] = (indices_flat[i] << 4) + indices_flat[i + 1]
    return bytes(buf)


def test_pack_matches_waveshare_on_codes():
    rng = np.random.default_rng(2)
    idx = rng.integers(0, 6, (600, 400), dtype=np.uint8)
    codes = palette.lut()[idx].ravel().tolist()
    assert pipeline.pack_indices(idx) == waveshare_pack(codes)


def test_lut_padding_maps_to_black():
    lut = palette.lut()
    assert list(lut[:6]) == [0, 1, 2, 3, 5, 6] and set(lut[6:]) == {0}


def test_yuv_roundtrip_and_screen_frame():
    img = gradient()
    yuv = pipeline.rgb_to_yuv420(img)
    assert yuv.shape == (972, 1152)
    y, u, v = pipeline.split_yuv420(yuv)
    assert y.shape == (648, 1152) and u.shape == (324, 576) and v.shape == (324, 576)
    back = pipeline.yuv_to_rgb(y, u, v, crop=False)
    diff = np.abs(np.asarray(back, dtype=int) - np.asarray(img, dtype=int)).mean()
    assert diff < 6
    cropped = pipeline.yuv_to_rgb(y, u, v, crop=True)
    assert cropped.size == (972, 648)
    frame = pipeline.screen_frame(cropped)
    assert len(frame) == epdfile.FRAME_BYTES
    assert set(epdfile.codes(frame)) <= set(palette.CODES)


def test_dither_indices_stay_in_range():
    img = gradient(600, 400)
    q = img.quantize(palette=palette.palette_image(), dither=Image.Dither.FLOYDSTEINBERG)
    assert int(np.asarray(q).max()) <= 5


def test_frame_orientation_roundtrip():
    # A red block in the top-left of the landscape picture stays there after encode → decode.
    img = Image.new("RGB", (972, 648), (161, 164, 165))
    img.paste((156, 72, 75), (0, 0, 200, 200))
    frame = pipeline.screen_frame(img)
    out = np.asarray(pipeline.frame_to_image(frame))
    assert out.shape == (400, 600, 3)
    red = palette.DISPLAY_RGB[3]
    assert tuple(out[20, 20]) == red
    assert tuple(out[380, 580]) != red


def test_from_lores_and_from_jpeg(tmp_path):
    img = gradient()
    epd, thumb = pipeline.from_lores(pipeline.rgb_to_yuv420(img))
    assert epdfile.unpack(epd)[1] == palette.PALETTE_VERSION
    assert Image.open(__import__("io").BytesIO(thumb)).size == (480, 270)
    full = img.resize((4608, 2592))
    jpg = tmp_path / "p.jpg"
    jpg.write_bytes(pipeline.encode_jpeg(full))
    epd2, thumb2 = pipeline.from_jpeg(jpg)
    assert len(epd2) == epdfile.FILE_BYTES and thumb2[:2] == b"\xff\xd8"


def test_exif_dict_and_info(tmp_path):
    from datetime import datetime

    import piexif

    ex = pipeline.exif_dict(datetime(2026, 10, 15, 14, 30, 22),
                            {"ExposureTime": 16667, "AnalogueGain": 2.0, "DigitalGain": 1.0})
    assert ex["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:10:15 14:30:22"
    jpg = tmp_path / "e.jpg"
    jpg.write_bytes(pipeline.encode_jpeg(gradient(64, 36), ex))
    info = pipeline.exif_info(jpg)
    assert info == {"exposure_s": pytest.approx(0.016667), "iso": 200}
    nodate = pipeline.exif_dict(None, {})
    assert piexif.ExifIFD.DateTimeOriginal not in nodate["Exif"]


def test_wifi_qr_escaping():
    assert share_screen.wifi_qr_text('a;b,c:d"e\\f', "p@ss;word") == \
        'WIFI:T:WPA;S:a\\;b\\,c\\:d\\"e\\\\f;P:p@ss\\;word;H:false;;'


def test_share_screens_render():
    away = share_screen.render_away("Inkstant-4F2A", "abcd2345efgh")
    home = share_screen.render_home("http://192.168.1.23/", "Neyra Home")
    for img in (away, home):
        assert img.size == (600, 400)
        arr = np.asarray(img)
        assert set(np.unique(arr)) <= {0, 255} or arr.min() == 0
    frame = share_screen.frame_away("Inkstant-4F2A", "abcd2345efgh")
    assert set(epdfile.codes(frame)) <= {0, 1}
