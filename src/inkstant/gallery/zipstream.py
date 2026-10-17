"""A streaming ZIP writer.

Stored (no compression: JPEGs don't shrink), no data descriptors (some readers that walk
local headers can't handle stored entries with them), and an exact length known before the
first byte, so phones can show progress. ZIP64 records only when needed.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ZIP32_LIMIT = 0xFFFFFFFF
ENTRY_LIMIT = 0xFFFF
CHUNK = 256 * 1024


class FileChanged(Exception):
    """A file vanished or changed size after the download started."""


@dataclass(frozen=True)
class Entry:
    name: str  # name inside the ZIP (ASCII)
    path: Path
    size: int
    when: datetime


def _dos(when: datetime) -> tuple[int, int]:
    y = min(max(when.year, 1980), 2107)
    t = (when.hour << 11) | (when.minute << 5) | (when.second // 2)
    d = ((y - 1980) << 9) | (when.month << 5) | when.day
    return t, d


class ZipPlan:
    def __init__(self, entries: list[Entry], zip64_threshold: int = ZIP32_LIMIT, entry_limit: int = ENTRY_LIMIT):
        self.entries = entries
        self.threshold = zip64_threshold
        self.entry_limit = entry_limit
        self._layout()

    def _big(self, n: int) -> bool:
        return n >= self.threshold

    def _layout(self) -> None:
        offset = 0
        self.offsets = []
        self.local_z64 = []
        for e in self.entries:
            self.offsets.append(offset)
            z = self._big(e.size)
            self.local_z64.append(z)
            offset += 30 + len(e.name.encode()) + (20 if z else 0) + e.size
        self.cd_offset = offset
        cd = 0
        self.central_extra = []
        for e, off in zip(self.entries, self.offsets, strict=True):
            fields = (2 if self._big(e.size) else 0) + (1 if self._big(off) else 0)
            extra = 4 + 8 * fields if fields else 0
            self.central_extra.append(extra)
            cd += 46 + len(e.name.encode()) + extra
        self.cd_size = cd
        self.zip64 = (len(self.entries) > self.entry_limit or self._big(self.cd_offset)
                      or self._big(self.cd_size) or any(self.local_z64) or any(self.central_extra))
        self.total = self.cd_offset + self.cd_size + (56 + 20 if self.zip64 else 0) + 22

    # Records ----------------------------------------------------------------------------
    def _local(self, i: int, crc: int) -> bytes:
        e = self.entries[i]
        name = e.name.encode()
        t, d = _dos(e.when)
        z = self.local_z64[i]
        size32 = ZIP32_LIMIT if z else e.size
        extra = struct.pack("<HHQQ", 0x0001, 16, e.size, e.size) if z else b""
        return struct.pack("<IHHHHHIIIHH", 0x04034B50, 45 if z else 10, 0, 0, t, d, crc,
                           size32, size32, len(name), len(extra)) + name + extra

    def _central(self, i: int, crc: int) -> bytes:
        e = self.entries[i]
        off = self.offsets[i]
        name = e.name.encode()
        t, d = _dos(e.when)
        big_size, big_off = self._big(e.size), self._big(off)
        vals = ([e.size, e.size] if big_size else []) + ([off] if big_off else [])
        extra = struct.pack("<HH", 0x0001, 8 * len(vals)) + b"".join(struct.pack("<Q", v) for v in vals) if vals else b""
        need = 45 if vals else 10
        return struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, (3 << 8) | need, need, 0, 0, t, d, crc,
                           ZIP32_LIMIT if big_size else e.size, ZIP32_LIMIT if big_size else e.size,
                           len(name), len(extra), 0, 0, 0, (0o100644 << 16), ZIP32_LIMIT if big_off else off) \
            + name + extra

    def _end(self) -> bytes:
        n = len(self.entries)
        out = b""
        if self.zip64:
            z64_offset = self.cd_offset + self.cd_size
            out += struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, (3 << 8) | 45, 45, 0, 0, n, n,
                               self.cd_size, self.cd_offset)
            out += struct.pack("<IIQI", 0x07064B50, 0, z64_offset, 1)
        out += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, min(n, 0xFFFF), min(n, 0xFFFF),
                           min(self.cd_size, ZIP32_LIMIT) if not self._big(self.cd_size) else ZIP32_LIMIT,
                           ZIP32_LIMIT if self.zip64 and self._big(self.cd_offset) else self.cd_offset, 0)
        return out

    # Streaming --------------------------------------------------------------------------------
    def stream(self) -> Iterator[bytes]:
        crcs = []
        for i, e in enumerate(self.entries):
            try:
                data = e.path.read_bytes()
            except OSError as err:
                raise FileChanged(str(err)) from err
            if len(data) != e.size:
                raise FileChanged(f"{e.name} changed size")
            crc = zlib.crc32(data) & 0xFFFFFFFF
            crcs.append(crc)
            yield self._local(i, crc)
            for start in range(0, len(data), CHUNK):
                yield data[start:start + CHUNK]
        yield b"".join(self._central(i, c) for i, c in enumerate(crcs))
        yield self._end()


def plan(files: list[tuple[str, Path, datetime]], **kw) -> ZipPlan:
    """Snapshot the files (stat each now) and lay out the archive."""
    entries = []
    for name, path, when in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entries.append(Entry(name, path, size, when))
    return ZipPlan(entries, **kw)
