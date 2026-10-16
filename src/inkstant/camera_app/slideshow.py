"""Scheduling the next slideshow wake (SPEC.md §8.12) and spotting a timed wake (§8.11).

Pure functions, standard library only. Local times use the system's time zone rules
through time.localtime/mktime, so daylight-saving changes are handled.
"""

from __future__ import annotations

import time

MIN_LEAD_S = 120  # the alarm must fire after the PiSugar has cut power
WAKE_EARLY_S = 10
WAKE_LATE_S = 60


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_quiet_hours(epoch: float, quiet: dict | None) -> bool:
    if not quiet:
        return False
    lt = time.localtime(epoch)
    tod = lt.tm_hour * 60 + lt.tm_min
    start, end = _minutes(quiet["start"]), _minutes(quiet["end"])
    if start < end:
        return start <= tod < end
    return tod >= start or tod < end  # crosses midnight


def quiet_end_after(epoch: float, quiet: dict) -> float:
    """The first end of the quiet window at or after `epoch` (which is inside the window)."""
    lt = time.localtime(epoch)
    end = _minutes(quiet["end"])
    for day in range(0, 3):
        candidate = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day, end // 60, end % 60, 0, 0, 0, -1))
        if candidate >= epoch:
            return candidate
    return epoch


def next_wake(now: float, settings: dict, battery_level: float | None, plugged: bool,
              clock_valid: bool, has_photos: bool) -> float | None:
    """Unix time of the next slideshow wake, or None when the camera shouldn't wake itself."""
    ss = settings["slideshow"]
    if not ss["enabled"] or not clock_valid or not has_photos:
        return None
    if not plugged and battery_level is not None and battery_level <= settings["low_battery_shutdown_pct"] + 2:
        return None
    t = now + max(ss["interval_min"] * 60, MIN_LEAD_S)
    if in_quiet_hours(t, ss["quiet_hours"]):
        t = max(quiet_end_after(t, ss["quiet_hours"]), now + MIN_LEAD_S)
    return t


def is_timed_wake(boot_utc: float, next_wake_utc: float | None) -> bool:
    """A start within the window after the armed alarm is a timed wake (no alarm flag exists)."""
    if next_wake_utc is None:
        return False
    return next_wake_utc - WAKE_EARLY_S <= boot_utc <= next_wake_utc + WAKE_LATE_S
