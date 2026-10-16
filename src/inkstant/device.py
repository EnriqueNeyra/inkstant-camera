"""Per-build hardware settings, /etc/inkstant/device.toml (SPEC.md §5.3)."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Device:
    model: str = "unknown"
    family: str = "pi3"  # pi3, pi4 or pi5
    wifi_5ghz: bool = True
    camera_hflip: bool = False
    camera_vflip: bool = False
    display_rotation: int = 0  # 0 or 180
    camera_port: str = "cam1"
    camera_fps: int | None = None
    errors: tuple[str, ...] = field(default=(), compare=False)


def family_from_model(model: str) -> str | None:
    """Map /proc/device-tree/model to a config.txt family (SPEC.md §15.1)."""
    m = model.lower()
    if "raspberry pi 5 model b" in m:  # not the Pi 500 keyboard (no camera port)
        return "pi5"
    if "raspberry pi 4 model b" in m:
        return "pi4"
    if any(s in m for s in ("raspberry pi 3 model b plus", "raspberry pi 3 model a plus", "raspberry pi 3 model b")):
        return "pi3"
    return None


def load(path: Path) -> Device:
    try:
        raw = tomllib.loads(Path(path).read_text())
    except FileNotFoundError:
        return Device(errors=(f"{path} not found; using defaults",))
    except (OSError, tomllib.TOMLDecodeError) as e:
        return Device(errors=(f"{path}: {e}; using defaults",))
    errors: list[str] = []

    def get(key, kind, default, check=lambda v: True):
        if key not in raw:
            return default
        value = raw[key]
        if not isinstance(value, kind) or isinstance(value, bool) and kind is int or not check(value):
            errors.append(f"device.toml: bad {key} = {value!r}; using {default!r}")
            return default
        return value

    fps = raw.get("camera_fps")
    if fps is not None and not (isinstance(fps, int) and not isinstance(fps, bool) and 1 <= fps <= 14):
        errors.append(f"device.toml: bad camera_fps = {fps!r}; ignored")
        fps = None
    return Device(
        model=get("model", str, "unknown"),
        family=get("family", str, "pi3", lambda v: v in ("pi3", "pi4", "pi5")),
        wifi_5ghz=get("wifi_5ghz", bool, True),
        camera_hflip=get("camera_hflip", bool, False),
        camera_vflip=get("camera_vflip", bool, False),
        display_rotation=get("display_rotation", int, 0, lambda v: v in (0, 180)),
        camera_port=get("camera_port", str, "cam1", lambda v: v in ("cam0", "cam1")),
        camera_fps=fps,
        errors=tuple(errors),
    )


def dumps(d: Device) -> str:
    """The file the install script writes."""
    lines = [
        "# Inkstant per-build hardware settings (SPEC.md §5.3). Written by the install script;",
        "# change by hand only when the build changes, then restart the camera.",
        f"model = {json.dumps(d.model)}",
        f"family = {json.dumps(d.family)}",
        f"wifi_5ghz = {str(d.wifi_5ghz).lower()}",
        f"camera_hflip = {str(d.camera_hflip).lower()}",
        f"camera_vflip = {str(d.camera_vflip).lower()}",
        f"display_rotation = {d.display_rotation}",
        f"camera_port = {json.dumps(d.camera_port)}",
    ]
    if d.camera_fps is not None:
        lines.append(f"camera_fps = {d.camera_fps}")
    else:
        lines.append("# camera_fps = 5   # optional override of the release's frame rate")
    return "\n".join(lines) + "\n"
