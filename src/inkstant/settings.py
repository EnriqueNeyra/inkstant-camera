"""User settings, /var/lib/inkstant/settings.json (SPEC.md §5.4, §1.8).

Standard library only: the install script uses it too.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from inkstant import atomic

SCHEMA_VERSION = 1

DEFAULTS: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "slideshow": {"enabled": True, "interval_min": 60, "order": "shuffle", "quiet_hours": None},
    "sleep_min": 5,
    "auto_off_min": 10,
    "battery_saver": {"enabled": False, "auto": True},
    "share_timeout_min": 10,
    "low_battery_shutdown_pct": 5,
    "charge_limit": False,
    "hotspot": {"ssid": "Inkstant", "password": "inkstant-camera"},
    "dev_mode": False,
}

# What the gallery offers (SPEC.md §1.8); the server accepts any value in range.
OPTIONS = {
    "slideshow.interval_min": [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440],
    "sleep_min": [1, 2, 3, 5, 10, 15, None],
    "auto_off_min": [1, 2, 5, 10, 15, 20, 30, 45, 60],
    "share_timeout_min": [5, 10, 15, 20, 30, 45, 60],
    "low_battery_shutdown_pct": [4, 5, 8, 10, 15, 20],
}

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsError(ValueError):
    """A bad value; `field` names the setting, the message says what's wrong in plain words."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def _int(field: str, value: Any, lo: int, hi: int, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(field, "Must be a whole number.")
    if not lo <= value <= hi:
        raise SettingsError(field, f"Must be between {lo} and {hi}.")
    return value


def _bool(field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise SettingsError(field, "Must be on or off.")
    return value


def validate_ssid(value: Any, field: str = "hotspot.ssid") -> str:
    if not isinstance(value, str) or not value:
        raise SettingsError(field, "Enter a network name.")
    n = len(value.encode("utf-8"))
    if n > 32:
        raise SettingsError(field, "Network names can be at most 32 bytes long.")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise SettingsError(field, "Network names can't contain control characters.")
    return value


def validate_wpa_password(value: Any, field: str = "hotspot.password") -> str:
    if not isinstance(value, str):
        raise SettingsError(field, "Enter a password.")
    if not 8 <= len(value) <= 63:
        raise SettingsError(field, "Passwords must be 8 to 63 characters.")
    if any(not 0x20 <= ord(c) <= 0x7E for c in value):
        raise SettingsError(field, "Use only letters, digits, spaces and common symbols.")
    return value


def _quiet_hours(value: Any) -> dict | None:
    field = "slideshow.quiet_hours"
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise SettingsError(field, "Give a start and an end time.")
    start, end = value["start"], value["end"]
    for v in (start, end):
        if not isinstance(v, str) or not _HHMM.match(v):
            raise SettingsError(field, "Times must look like 23:00.")
    if start == end:
        raise SettingsError(field, "The start and end times must differ.")
    return {"start": start, "end": end}


def validate(s: dict[str, Any]) -> dict[str, Any]:
    """Check a complete settings dict; returns a clean copy or raises SettingsError."""
    out = copy.deepcopy(DEFAULTS)
    ss = s.get("slideshow", {})
    if not isinstance(ss, dict):
        raise SettingsError("slideshow", "Bad slideshow settings.")
    out["slideshow"]["enabled"] = _bool("slideshow.enabled", ss.get("enabled", True))
    out["slideshow"]["interval_min"] = _int("slideshow.interval_min", ss.get("interval_min", 60), 5, 1440)
    order = ss.get("order", "shuffle")
    if order not in ("shuffle", "newest"):
        raise SettingsError("slideshow.order", "Choose shuffle or newest first.")
    out["slideshow"]["order"] = order
    out["slideshow"]["quiet_hours"] = _quiet_hours(ss.get("quiet_hours"))
    out["sleep_min"] = _int("sleep_min", s.get("sleep_min", 5), 1, 15, nullable=True)
    out["auto_off_min"] = _int("auto_off_min", s.get("auto_off_min", 10), 1, 60)
    bs = s.get("battery_saver", {})
    if not isinstance(bs, dict):
        raise SettingsError("battery_saver", "Bad battery saver settings.")
    out["battery_saver"]["enabled"] = _bool("battery_saver.enabled", bs.get("enabled", False))
    out["battery_saver"]["auto"] = _bool("battery_saver.auto", bs.get("auto", True))
    out["share_timeout_min"] = _int("share_timeout_min", s.get("share_timeout_min", 10), 5, 60)
    out["low_battery_shutdown_pct"] = _int("low_battery_shutdown_pct", s.get("low_battery_shutdown_pct", 5), 4, 20)
    out["charge_limit"] = _bool("charge_limit", s.get("charge_limit", False))
    hs = s.get("hotspot", {})
    if not isinstance(hs, dict):
        raise SettingsError("hotspot", "Bad camera Wi-Fi settings.")
    out["hotspot"]["ssid"] = validate_ssid(hs.get("ssid", DEFAULTS["hotspot"]["ssid"]))
    out["hotspot"]["password"] = validate_wpa_password(hs.get("password", DEFAULTS["hotspot"]["password"]))
    out["dev_mode"] = _bool("dev_mode", s.get("dev_mode", False))
    out["version"] = SCHEMA_VERSION
    return out


def merge(base: dict[str, Any], partial: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update (nested dicts merge; other values replace) and validate."""
    if not isinstance(partial, dict):
        raise SettingsError("", "Expected an object.")
    merged = copy.deepcopy(base)
    for key, value in partial.items():
        if key not in DEFAULTS or key == "version":
            raise SettingsError(key, f"Unknown setting {key!r}.")
        if isinstance(DEFAULTS[key], dict):
            if not isinstance(value, dict):
                raise SettingsError(key, "Expected an object.")
            for sub in value:
                if sub not in DEFAULTS[key]:
                    raise SettingsError(f"{key}.{sub}", f"Unknown setting {key}.{sub!r}.")
            merged[key] = {**merged.get(key, {}), **value}
        else:
            merged[key] = value
    return validate(merged)


def load(path: Path, log=None) -> dict[str, Any]:
    raw, err = atomic.read_json(path)
    if err and log:
        log(f"settings: {err}")
    if raw is None:
        return copy.deepcopy(DEFAULTS)
    try:
        return validate(raw)
    except SettingsError as e:
        if log:
            log(f"settings: {e.field}: {e.message}; resetting that field to its default")
        # Keep what's valid: retry dropping the bad field.
        fixed = copy.deepcopy(raw)
        top = e.field.split(".")[0]
        sub = e.field.split(".")[1] if "." in e.field else None
        if sub and isinstance(fixed.get(top), dict):
            fixed[top].pop(sub, None)
        else:
            fixed.pop(top, None)
        try:
            return validate(fixed)
        except SettingsError:
            return copy.deepcopy(DEFAULTS)


def save(path: Path, settings: dict[str, Any], tmp_dir: Path | None = None) -> None:
    atomic.write_json(path, validate(settings), mode=0o600, tmp_dir=tmp_dir)


def public(settings: dict[str, Any]) -> dict[str, Any]:
    """What the gallery may see: everything (the hotspot password is on the share screen anyway)."""
    return copy.deepcopy(settings)
