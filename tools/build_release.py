#!/usr/bin/env python3
"""Build a release: dist/inkstant.tar.gz (top folder inkstant/) and dist/SHA256SUMS (SPEC.md §16.1).

    python3 tools/build_release.py

The tarball holds VERSION, src/ (with _build.py: VERSION and BUILD_EPOCH = now), bin/, system/,
install/ and tools/. Upload both files to a GitHub release tagged v<VERSION>.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["VERSION", "README.md", "SPEC.md", "HARDWARE.md", "src", "bin", "system", "install", "tools"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def add_tree(tar: tarfile.TarFile, path: Path, arc: str) -> None:
    if any(p in SKIP_PARTS for p in path.parts) or path.suffix == ".pyc" or path.name == "_build.py":
        return
    if path.is_dir():
        info = tar.gettarinfo(str(path), arc)
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        tar.addfile(info)
        for child in sorted(path.iterdir()):
            add_tree(tar, child, f"{arc}/{child.name}")
        return
    info = tar.gettarinfo(str(path), arc)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    with open(path, "rb") as f:
        tar.addfile(info, f)


def main() -> int:
    version = (ROOT / "VERSION").read_text().strip()
    epoch = int(time.time())
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / "inkstant.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for name in INCLUDE:
            p = ROOT / name
            if p.exists():
                add_tree(tar, p, f"inkstant/{name}")
        build = f"VERSION = {version!r}\nBUILD_EPOCH = {epoch}\n".encode()
        info = tarfile.TarInfo("inkstant/src/inkstant/_build.py")
        info.size = len(build)
        info.mode = 0o644
        info.mtime = epoch
        tar.addfile(info, io.BytesIO(build))
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (dist / "SHA256SUMS").write_text(f"{digest}  inkstant.tar.gz\n")
    print(f"built {out} ({out.stat().st_size // 1024} KB), version {version}, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
