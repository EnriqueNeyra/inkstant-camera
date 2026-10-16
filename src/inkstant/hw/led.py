"""The status light: one steady pattern, a wink, and cues (SPEC.md §1.3, §6.2).

The kernel's `gpio-led` driver owns GPIO 26; patterns are written to
/sys/class/leds/status. Without the `pattern` trigger, an asyncio task blinks instead.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

# name: (trigger, value). For "pattern" the value is the kernel pattern string.
STEADY = {
    "starting": ("timer", None),
    "ready": ("none", 1),
    "off": ("none", 0),
    "wait": ("pattern", "1 500 1 0 0 500 0 0"),
    "share": ("pattern", "1 1700 1 0 0 300 0 0"),
    "asleep": ("pattern", "1 100 1 0 0 3900 0 0"),
    "problem": ("pattern", "1 100 1 0 0 150 0 0 1 100 1 0 0 150 0 0 1 100 1 0 0 1900 0 0"),
}
# name: (pattern, repeat, seconds)
CUES = {
    "no_module": ("1 125 1 0 0 125 0 0", 8, 2.0),
    "battery": ("1 100 1 0 0 150 0 0 1 100 1 0 0 650 0 0", 3, 3.0),
}
CUE_OK = frozenset({"ready", "asleep", "share"})
WINK_SECONDS = 0.4


def pattern_steps(pattern: str) -> list[tuple[int, float]]:
    """Kernel pattern string → (brightness, seconds) steps, skipping the zero-length ones."""
    nums = [int(x) for x in pattern.split()]
    return [(nums[i], nums[i + 1] / 1000) for i in range(0, len(nums), 2) if nums[i + 1] > 0]


class SysfsLed:
    """Writes /sys/class/leds/status/*."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._has_pattern: bool | None = None

    def _write(self, name: str, value: str) -> None:
        (self.path / name).write_text(value)

    def triggers(self) -> list[str]:
        try:
            text = (self.path / "trigger").read_text()
        except OSError:
            return []
        return [t.strip("[]") for t in text.split()]

    def has_pattern(self) -> bool:
        if self._has_pattern is None:
            self._has_pattern = "pattern" in self.triggers()
        return self._has_pattern

    def set_level(self, value: int) -> None:
        self._write("trigger", "none")
        self._write("brightness", str(value))

    def set_timer(self) -> None:
        self._write("trigger", "timer")

    def set_pattern(self, pattern: str, repeat: int) -> None:
        self._write("trigger", "pattern")
        self._write("pattern", pattern)
        self._write("repeat", str(repeat))

    def brightness(self, value: int) -> None:
        self._write("brightness", str(value))


class FakeLed:
    """Records what would be written (tests, simulator)."""

    def __init__(self, has_pattern: bool = True):
        self._has = has_pattern
        self.log: list[tuple] = []
        self.level = 0

    def has_pattern(self) -> bool:
        return self._has

    def set_level(self, value: int) -> None:
        self.level = value
        self.log.append(("level", value))

    def set_timer(self) -> None:
        self.log.append(("timer",))

    def set_pattern(self, pattern: str, repeat: int) -> None:
        self.log.append(("pattern", pattern, repeat))

    def brightness(self, value: int) -> None:
        self.level = value


class LedController:
    """Decides what the light shows right now. All methods run on the event loop."""

    def __init__(self, writer, loop: asyncio.AbstractEventLoop | None = None, log=None):
        self.w = writer
        self.loop = loop
        self.log = log or (lambda *_: None)
        self.steady = "starting"
        self.playing: str | None = None  # "wink" or a cue name
        self.queue: deque[str] = deque()
        self._timer: asyncio.TimerHandle | None = None
        self._soft_task: asyncio.Task | None = None
        self.shown = "starting"  # what's on the light now: a steady name, "wink" or a cue

    def _loop(self) -> asyncio.AbstractEventLoop:
        return self.loop or asyncio.get_running_loop()

    # Writing ---------------------------------------------------------------
    def _stop_soft(self) -> None:
        if self._soft_task:
            self._soft_task.cancel()
            self._soft_task = None

    def _soft(self, pattern: str, repeat: int) -> None:
        steps = pattern_steps(pattern)

        async def run():
            n = 0
            while repeat < 0 or n < repeat:
                for level, secs in steps:
                    self.w.brightness(level)
                    await asyncio.sleep(secs)
                n += 1

        self._soft_task = self._loop().create_task(run())

    def _write_pattern(self, pattern: str, repeat: int) -> None:
        self._stop_soft()
        try:
            if self.w.has_pattern():
                self.w.set_pattern(pattern, repeat)
            else:
                self.w.set_level(0)
                self._soft(pattern, repeat)
        except OSError as e:
            self.log(f"led: {e}")

    def _show_steady(self) -> None:
        self._stop_soft()
        trigger, value = STEADY[self.steady]
        self.shown = self.steady
        try:
            if trigger == "none":
                self.w.set_level(value)
            elif trigger == "timer":
                self.w.set_timer()
            else:
                self._write_pattern(value, -1)
        except OSError as e:
            self.log(f"led: {e}")

    # Public ------------------------------------------------------------------
    def set_steady(self, name: str) -> None:
        if name not in STEADY:
            raise ValueError(name)
        changed = name != self.steady
        self.steady = name
        if name not in CUE_OK:
            self.queue.clear()
            if self.playing not in (None, "wink"):
                self._end_overlay(show=False)
        if self.playing is None and (changed or self.shown != name):
            self._show_steady()

    def wink(self) -> None:
        self._cancel_timer()
        self.playing = "wink"
        self.shown = "wink"
        self._stop_soft()
        try:
            self.w.set_level(0)
        except OSError as e:
            self.log(f"led: {e}")
        self._timer = self._loop().call_later(WINK_SECONDS, self._next)

    def cue(self, name: str) -> None:
        if name not in CUES:
            raise ValueError(name)
        if self.steady not in CUE_OK:
            return
        self.queue.append(name)
        if self.playing is None:
            self._next()

    # Internals --------------------------------------------------------------
    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _end_overlay(self, show: bool) -> None:
        self._cancel_timer()
        self.playing = None
        if show:
            self._show_steady()

    def _next(self) -> None:
        self._timer = None
        self.playing = None
        while self.queue:
            name = self.queue.popleft()
            if self.steady not in CUE_OK:
                self.queue.clear()
                break
            pattern, repeat, secs = CUES[name]
            self.playing = name
            self.shown = name
            self._write_pattern(pattern, repeat)
            self._timer = self._loop().call_later(secs, self._next)
            return
        self._show_steady()

    def close(self) -> None:
        self._cancel_timer()
        self._stop_soft()
