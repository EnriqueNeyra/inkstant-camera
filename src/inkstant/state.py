"""Runtime state: state.json and slideshow.json (SPEC.md §5.5)."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from inkstant import atomic

DISPLAY_CONTENT = ("photo", "share", "blank", "unknown")


@dataclass
class State:
    next_seq: int = 1
    undeveloped: str | None = None
    current: str | None = None
    display_content: str = "unknown"
    next_wake_utc: float | None = None
    last_shutdown: dict[str, Any] | None = None
    saver_auto_active: bool = False
    saver_auto_dismissed: bool = False
    version: int = 1

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> State:
        s = cls()
        if not isinstance(d, dict):
            return s
        if isinstance(d.get("next_seq"), int) and d["next_seq"] >= 1:
            s.next_seq = d["next_seq"]
        for key in ("undeveloped", "current"):
            if d.get(key) is None or isinstance(d.get(key), str):
                setattr(s, key, d.get(key))
        if d.get("display_content") in DISPLAY_CONTENT:
            s.display_content = d["display_content"]
        if isinstance(d.get("next_wake_utc"), (int, float)) and not isinstance(d.get("next_wake_utc"), bool):
            s.next_wake_utc = float(d["next_wake_utc"])
        if isinstance(d.get("last_shutdown"), dict):
            s.last_shutdown = d["last_shutdown"]
        for key in ("saver_auto_active", "saver_auto_dismissed"):
            if isinstance(d.get(key), bool):
                setattr(s, key, d[key])
        return s


def load_state(path: Path, log=None) -> State:
    raw, err = atomic.read_json(path)
    if err and log:
        log(f"state: {err}")
    return State.from_dict(raw) if raw is not None else State()


def save_state(path: Path, state: State, tmp_dir: Path | None = None) -> None:
    atomic.write_json(path, asdict(state), tmp_dir=tmp_dir)


@dataclass
class SlideshowState:
    """A shuffle bag of sequence numbers (§5.5)."""

    bag: list[int] = field(default_factory=list)
    last: int | None = None

    @classmethod
    def from_dict(cls, d: Any) -> SlideshowState:
        if not isinstance(d, dict):
            return cls()
        bag = [x for x in d.get("bag", []) if isinstance(x, int) and not isinstance(x, bool)]
        last = d.get("last") if isinstance(d.get("last"), int) else None
        return cls(bag=bag, last=last)

    def next(self, available: list[int], order: str, rng: random.Random | None = None) -> int | None:
        """The next photo to show, from `available` sequence numbers. Mutates the bag."""
        if not available:
            return None
        avail = sorted(set(available))
        if order == "newest":
            older = [s for s in avail if self.last is not None and s < self.last]
            choice = older[-1] if older else avail[-1]
            self.last = choice
            return choice
        rng = rng or random.Random()
        present = set(avail)
        while True:
            # Drop photos deleted since they went into the bag.
            self.bag = [s for s in self.bag if s in present]
            if not self.bag:
                refill = [s for s in avail if s != self.last] or avail[:]
                rng.shuffle(refill)
                self.bag = refill
            choice = self.bag.pop(0)
            if choice in present:
                self.last = choice
                return choice

    def add(self, seq: int, rng: random.Random | None = None) -> None:
        """A new photo goes into the bag at a random position."""
        rng = rng or random.Random()
        if seq in self.bag:
            return
        self.bag.insert(rng.randint(0, len(self.bag)), seq)


def load_slideshow(path: Path) -> SlideshowState:
    raw, _ = atomic.read_json(path)
    return SlideshowState.from_dict(raw)


def save_slideshow(path: Path, s: SlideshowState, tmp_dir: Path | None = None) -> None:
    atomic.write_json(path, {"bag": s.bag, "last": s.last}, keep_backup=False, tmp_dir=tmp_dir)
