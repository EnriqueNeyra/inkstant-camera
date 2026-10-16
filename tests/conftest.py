import os
import tempfile
from pathlib import Path

import pytest

from inkstant.paths import Paths


@pytest.fixture
def paths():
    # Short root: Unix socket paths are limited to about 100 bytes on macOS.
    with tempfile.TemporaryDirectory(prefix="ink", dir="/tmp") as d:
        p = Paths(Path(d))
        p.ensure_dirs()
        yield p


@pytest.fixture(autouse=True)
def _no_root_env(monkeypatch):
    monkeypatch.delenv("INKSTANT_ROOT", raising=False)
    os.environ.setdefault("GPIOZERO_PIN_FACTORY", "mock")
