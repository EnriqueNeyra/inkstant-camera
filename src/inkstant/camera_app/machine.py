"""The camera's rules as pure functions: what the light shows, whether the shutter
is accepted, timers, battery levels and the battery saver. No hardware, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

STARTING, WAKE, ACTIVE, SHUTTING_DOWN = "STARTING", "WAKE", "ACTIVE", "SHUTTING_DOWN"
SAVER_SLEEP_MIN = 1
SAVER_OFF_MIN = 5
SAVER_SHARE_MIN = 5


@dataclass
class View:
    """The state the rules look at."""

    lifecycle: str = STARTING
    camera: str = "off"  # off, starting, ready, fallback, asleep, waking, failed
    capture_busy: bool = False
    display: str = "idle"  # idle, refreshing, tail
    develop_pending: bool = False
    shutdown_display_busy: bool = False
    share: str = "off"  # off, starting, on, stopping
    storage: str = "ok"
    update_running: bool = False


def led_steady(v: View) -> str:
    """The status light: a slow blink while a tap won't take a photo because the camera is busy or
    sharing, solid otherwise, off when off. Only four things to learn. Problems (storage full, a
    failed camera) leave it solid: a refused tap just gets no wink, and the gallery says why. The
    battery shows in the gallery, and sharing on the print."""
    if v.lifecycle == STARTING:
        return "starting"
    if v.lifecycle == WAKE:
        return "off"
    if v.lifecycle == SHUTTING_DOWN:
        return "wait" if v.shutdown_display_busy else "off"
    if (v.display == "refreshing" or v.develop_pending or v.capture_busy
            or v.camera in ("starting", "waking") or v.update_running or v.share != "off"):
        return "wait"
    return "ready"


def shutter_accepted(v: View) -> bool:
    """Whether a shutter press takes a photo now."""
    return (v.lifecycle == ACTIVE and v.camera in ("ready", "fallback", "asleep", "waking")
            and not v.capture_busy and v.display != "refreshing" and v.storage != "full"
            and not v.update_running and v.share == "off")  # no shooting while sharing


def btn_accepted(v: View) -> bool:
    if v.lifecycle == WAKE:
        return True
    return v.lifecycle == ACTIVE and v.display != "refreshing" and not v.update_running


def saver_on(settings: dict, saver_auto_active: bool) -> bool:
    return bool(settings["battery_saver"]["enabled"] or saver_auto_active)


def effective_minutes(settings: dict, saver: bool) -> tuple[int | None, int, int]:
    """(sleep, turn off, share timeout) in minutes; sleep None = never."""
    sleep = settings["sleep_min"]
    off = settings["auto_off_min"]
    share = settings["share_timeout_min"]
    if saver:
        sleep = SAVER_SLEEP_MIN if sleep is None else min(sleep, SAVER_SLEEP_MIN)
        off = min(off, SAVER_OFF_MIN)
        share = min(share, SAVER_SHARE_MIN)
    if sleep is not None and sleep >= off:
        sleep = None  # it turns off before it would sleep
    return sleep, off, share


@dataclass(frozen=True)
class Levels:
    warn: float
    saver: float
    shutdown: float
    backup: float = 3.0


def battery_levels(settings: dict) -> Levels:
    """The warning and the saver's automatic level move up with a high shutdown level."""
    shut = settings["low_battery_shutdown_pct"]
    return Levels(warn=max(15, shut + 5), saver=max(20, shut + 5), shutdown=shut)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def saver_auto_update(settings: dict, level: float | None, plugged: bool,
                      active: bool, dismissed: bool) -> tuple[bool, bool]:
    """The battery saver's new (saver_auto_active, saver_auto_dismissed) after a battery reading."""
    if plugged:
        return False, False
    if not settings["battery_saver"]["auto"]:
        return False, dismissed
    if active:
        return True, dismissed
    if dismissed or level is None:
        return False, dismissed
    return level <= battery_levels(settings).saver, dismissed


def power_button_in_wake(level: float | None, plugged: bool) -> str:
    """During a slideshow wake, PWR means "turn on" unless it's the low-battery backup."""
    if not plugged and level is not None and level <= Levels(0, 0, 0).backup + 1:
        return "shutdown"
    return "turn_on"
