"""The photo store: IDs, commit, list, delete, free space."""

from __future__ import annotations

import contextlib
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from inkstant.atomic import atomic_write, fsync_dir
from inkstant.paths import Paths

ID_RE = re.compile(r"^\d{6}(_\d{8}-\d{6})?$")
LOW_BYTES = 1 << 30  # "storage low" below 1 GiB
FULL_BYTES = 500 << 20  # "storage full" below 500 MiB
DEFAULT_PHOTO_BYTES = 4_000_000
WEB_GROUP = "inkstant-web"


def make_id(seq: int, when: datetime | None) -> str:
    if not 1 <= seq <= 999_999:
        raise ValueError(f"sequence number out of range: {seq}")
    if when is None:
        return f"{seq:06d}"
    return f"{seq:06d}_{when.strftime('%Y%m%d-%H%M%S')}"


def valid_id(photo_id: object) -> bool:
    return isinstance(photo_id, str) and bool(ID_RE.match(photo_id))


def seq_of(photo_id: str) -> int:
    return int(photo_id[:6])


def when_of(photo_id: str) -> datetime | None:
    """The local time in the ID, or None when the clock was unset."""
    if len(photo_id) <= 6:
        return None
    try:
        return datetime.strptime(photo_id[7:], "%Y%m%d-%H%M%S")
    except ValueError:
        return None


@dataclass(frozen=True)
class Photo:
    id: str
    seq: int
    when: datetime | None
    size: int

    @property
    def day(self) -> str | None:
        return self.when.strftime("%Y-%m-%d") if self.when else None

    @property
    def time(self) -> str | None:
        return self.when.strftime("%H:%M") if self.when else None


class PhotoStore:
    def __init__(self, paths: Paths, *, web_group: str | None = WEB_GROUP):
        self.paths = paths
        self.web_group = web_group
        self._lock = threading.Lock()
        self._cache_key: int | None = None
        self._cache: list[Photo] = []

    # Paths ---------------------------------------------------------------
    def photo_path(self, photo_id: str) -> Path:
        return self.paths.photos / f"{photo_id}.jpg"

    def thumb_path(self, photo_id: str) -> Path:
        return self.paths.thumbs / f"{photo_id}.jpg"

    def print_path(self, photo_id: str) -> Path:
        """The print as the panel shows it, for the gallery (beside the thumbnail)."""
        return self.paths.thumbs / f"{photo_id}.png"

    def epd_path(self, photo_id: str) -> Path:
        return self.paths.epd / f"{photo_id}.epd"

    # Listing -------------------------------------------------------------
    def list(self) -> list[Photo]:
        """Every photo, newest first. Cached until the folder's mtime changes."""
        try:
            key = self.paths.photos.stat().st_mtime_ns
        except FileNotFoundError:
            return []
        with self._lock:
            if key == self._cache_key:
                return list(self._cache)
            photos = []
            with os.scandir(self.paths.photos) as it:
                for entry in it:
                    name = entry.name
                    if not name.endswith(".jpg"):
                        continue
                    pid = name[:-4]
                    if not valid_id(pid):
                        continue
                    try:
                        size = entry.stat().st_size
                    except FileNotFoundError:
                        continue
                    photos.append(Photo(pid, seq_of(pid), when_of(pid), size))
            photos.sort(key=lambda p: p.seq, reverse=True)
            self._cache, self._cache_key = photos, key
            return list(photos)

    def get(self, photo_id: str) -> Photo | None:
        if not valid_id(photo_id):
            return None
        try:
            size = self.photo_path(photo_id).stat().st_size
        except FileNotFoundError:
            return None
        return Photo(photo_id, seq_of(photo_id), when_of(photo_id), size)

    def by_seq(self, seq: int) -> Photo | None:
        for p in self.list():
            if p.seq == seq:
                return p
        return None

    def max_seq(self) -> int:
        photos = self.list()
        return photos[0].seq if photos else 0

    # Saving --------------------------------------------------------------
    def commit(self, photo_id: str, epd: bytes, thumb: bytes, jpeg: bytes, print_png: bytes) -> None:
        """Screen image, thumbnail and print, then the JPEG: a photo exists once its JPEG is in place."""
        if not valid_id(photo_id):
            raise ValueError(f"bad photo id {photo_id!r}")
        tmp = self.paths.tmp
        atomic_write(self.epd_path(photo_id), epd, mode=0o600, tmp_dir=tmp)
        atomic_write(self.thumb_path(photo_id), thumb, mode=0o640, group=self.web_group, tmp_dir=tmp)
        atomic_write(self.print_path(photo_id), print_png, mode=0o640, group=self.web_group, tmp_dir=tmp)
        atomic_write(self.photo_path(photo_id), jpeg, mode=0o640, group=self.web_group, tmp_dir=tmp)

    def write_epd(self, photo_id: str, epd: bytes) -> None:
        atomic_write(self.epd_path(photo_id), epd, mode=0o600, tmp_dir=self.paths.tmp)

    def write_thumb(self, photo_id: str, thumb: bytes) -> None:
        atomic_write(self.thumb_path(photo_id), thumb, mode=0o640, group=self.web_group, tmp_dir=self.paths.tmp)

    def write_print(self, photo_id: str, print_png: bytes) -> None:
        atomic_write(self.print_path(photo_id), print_png, mode=0o640, group=self.web_group, tmp_dir=self.paths.tmp)

    def delete(self, ids: list[str]) -> tuple[list[str], list[str]]:
        deleted, missing = [], []
        for pid in ids:
            if not valid_id(pid) or not self.photo_path(pid).exists():
                missing.append(pid)
                continue
            # JPEG first, so a crash midway leaves only orphans that recovery removes.
            for p in (self.photo_path(pid), self.thumb_path(pid), self.print_path(pid), self.epd_path(pid)):
                with contextlib.suppress(FileNotFoundError):
                    p.unlink()
            deleted.append(pid)
        for d in (self.paths.photos, self.paths.thumbs, self.paths.epd):
            with contextlib.suppress(OSError):
                fsync_dir(d)
        return deleted, missing

    # Recovery -----------------------------------------------------
    def recover(self) -> list[str]:
        """Clean up after a power cut. Returns IDs whose thumbnail, print or screen image must be rebuilt."""
        self.paths.ensure_dirs()
        for entry in self.paths.tmp.iterdir():
            with contextlib.suppress(OSError):
                if entry.is_file() or entry.is_symlink():
                    entry.unlink()
        jpegs = {p.id for p in self.list()}
        for folder, suffixes in ((self.paths.epd, (".epd",)), (self.paths.thumbs, (".jpg", ".png"))):
            for entry in folder.iterdir():
                stem, dot, ext = entry.name.rpartition(".")
                if f".{ext}" not in suffixes or not dot or not valid_id(stem) or stem not in jpegs:
                    with contextlib.suppress(OSError):
                        entry.unlink()
        rebuild = []
        for pid in sorted(jpegs, key=seq_of):
            if not all(p.exists() for p in (self.thumb_path(pid), self.print_path(pid), self.epd_path(pid))):
                rebuild.append(pid)
        return rebuild

    # Space ---------------------------------------------------------
    def disk(self) -> tuple[int, int]:
        st = os.statvfs(self.paths.var)
        return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize

    def storage_state(self, free: int | None = None) -> str:
        if free is None:
            free, _ = self.disk()
        if free < FULL_BYTES:
            return "full"
        if free < LOW_BYTES:
            return "low"
        return "ok"

    def photos_left(self, free: int | None = None) -> int:
        if free is None:
            free, _ = self.disk()
        recent = self.list()[:20]
        avg = sum(p.size for p in recent) / len(recent) if recent else DEFAULT_PHOTO_BYTES
        return max(0, int((free - FULL_BYTES) // max(avg, 1)))

    def used_bytes(self) -> int:
        return sum(p.size for p in self.list())
