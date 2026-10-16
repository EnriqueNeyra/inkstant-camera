"""Read exposure and ISO from a saved photo's EXIF. Light: the gallery imports it (piexif only)."""

from __future__ import annotations

from pathlib import Path


def exif_info(jpeg_path: Path) -> dict:
    import piexif

    try:
        ex = piexif.load(str(jpeg_path)).get("Exif", {})
    except Exception:
        return {}
    out = {}
    et = ex.get(piexif.ExifIFD.ExposureTime)
    if isinstance(et, tuple) and len(et) == 2 and et[1]:
        out["exposure_s"] = et[0] / et[1]
    iso = ex.get(piexif.ExifIFD.ISOSpeedRatings)
    if isinstance(iso, int):
        out["iso"] = iso
    elif isinstance(iso, tuple) and iso:
        out["iso"] = int(iso[0])
    return out
