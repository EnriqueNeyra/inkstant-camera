"""Release version and build time.

BUILD_EPOCH is the earliest time a working clock can show: an RTC reading before it
means the clock was lost. Release builds and from-source installs write `_build.py`
next to this file; the fallback below is this source tree's date.
"""

from pathlib import Path

_FALLBACK_EPOCH = 1792022400  # 2026-10-15T00:00:00Z


def _read_version() -> str:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            return candidate.read_text().strip()
    return "0.0.0"


try:
    from inkstant._build import BUILD_EPOCH, VERSION  # type: ignore[import-not-found]
except ImportError:
    VERSION = _read_version()
    BUILD_EPOCH = _FALLBACK_EPOCH
