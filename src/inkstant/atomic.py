"""Atomic file writes.

A power cut can happen at any moment, so every file is written under a temporary
name in the same filesystem, flushed to disk, then renamed into place.
"""

from __future__ import annotations

import contextlib
import grp
import itertools
import json
import os
from pathlib import Path
from typing import Any

_counter = itertools.count()


def _gid(group: str | None) -> int:
    if group is None:
        return -1
    try:
        return grp.getgrnam(group).gr_gid
    except KeyError:
        return -1


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(
    final_path: Path,
    data: bytes,
    *,
    mode: int = 0o600,
    group: str | None = None,
    tmp_dir: Path | None = None,
) -> None:
    """Write `data` to `final_path` so that it's either all there or not there at all."""
    final_path = Path(final_path)
    tmp_dir = Path(tmp_dir) if tmp_dir is not None else final_path.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{final_path.name}.{os.getpid()}.{next(_counter)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, mode)
        gid = _gid(group)
        if gid >= 0 and os.geteuid() == 0:
            os.fchown(fd, 0, gid)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    os.close(fd)
    try:
        os.rename(tmp, final_path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    fsync_dir(final_path.parent)


def write_json(path: Path, obj: Any, *, mode: int = 0o600, keep_backup: bool = True,
               tmp_dir: Path | None = None) -> None:
    """Write JSON atomically, keeping the previous version as `<name>.bak`."""
    path = Path(path)
    if keep_backup and path.exists():
        bak = path.with_name(path.name + ".bak")
        with contextlib.suppress(OSError):
            atomic_write(bak, path.read_bytes(), mode=mode, tmp_dir=tmp_dir)
    data = (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode()
    atomic_write(path, data, mode=mode, tmp_dir=tmp_dir)


def read_json(path: Path) -> tuple[Any, str | None]:
    """Read JSON, falling back to `.bak`. Returns (value or None, error message or None)."""
    path = Path(path)
    errors = []
    for candidate in (path, path.with_name(path.name + ".bak")):
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text()), "; ".join(errors) or None
        except (OSError, ValueError) as e:
            errors.append(f"{candidate.name}: {e}")
    return None, "; ".join(errors) or None
