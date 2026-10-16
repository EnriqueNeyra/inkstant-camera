"""Fixed paths (SPEC.md §4.4, §5).

Everything hangs off a root, "/" on the camera. Tests and the simulator use a
temporary root, set with the INKSTANT_ROOT environment variable or `Paths(root)`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def default(cls) -> Paths:
        return cls(Path(os.environ.get("INKSTANT_ROOT", "/")))

    # /var/lib/inkstant (§5.1)
    @property
    def var(self) -> Path:
        return self.root / "var/lib/inkstant"

    @property
    def photos(self) -> Path:
        return self.var / "photos"

    @property
    def thumbs(self) -> Path:
        return self.var / "thumbs"

    @property
    def epd(self) -> Path:
        return self.var / "epd"

    @property
    def tmp(self) -> Path:
        return self.var / "tmp"

    @property
    def backup(self) -> Path:
        return self.var / "backup"

    @property
    def settings(self) -> Path:
        return self.var / "settings.json"

    @property
    def state(self) -> Path:
        return self.var / "state.json"

    @property
    def slideshow(self) -> Path:
        return self.var / "slideshow.json"

    @property
    def selftest(self) -> Path:
        return self.var / "selftest.json"

    @property
    def update_json(self) -> Path:
        return self.var / "update.json"

    @property
    def install_state(self) -> Path:
        return self.var / "install-state.json"

    # Elsewhere
    @property
    def device_toml(self) -> Path:
        return self.root / "etc/inkstant/device.toml"

    @property
    def run_dir(self) -> Path:
        return self.root / "run/inkstant"

    @property
    def control_sock(self) -> Path:
        return self.run_dir / "control.sock"

    @property
    def pisugar_sock(self) -> Path:
        # pisugar-server's socket is always here on the camera; the simulator moves it.
        if self.root == Path("/"):
            return Path("/tmp/pisugar-server.sock")
        return self.root / "tmp/pisugar-server.sock"

    @property
    def opt(self) -> Path:
        return self.root / "opt/inkstant"

    @property
    def leds(self) -> Path:
        return self.root / "sys/class/leds/status"

    def ensure_dirs(self) -> None:
        for d in (self.var, self.photos, self.thumbs, self.epd, self.tmp, self.run_dir):
            d.mkdir(parents=True, exist_ok=True)
