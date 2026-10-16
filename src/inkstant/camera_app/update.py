"""Software update helpers (SPEC.md §16.1). Standard library only."""

from __future__ import annotations

import json
import re
import urllib.request

REPO = "EnriqueNeyra/inkstant-camera"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_ASSET = "inkstant.tar.gz"


def parse(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def newer(latest: str, current: str) -> bool:
    return parse(latest) > parse(current)


def check_latest(timeout: float = 8.0) -> dict:
    """The latest non-pre-release on GitHub: {"version", "url", "notes"}. Raises OSError."""
    req = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "inkstant-camera"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if data.get("prerelease") or data.get("draft"):
        raise OSError("no release yet")
    tag = data.get("tag_name", "")
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets", [])}
    if RELEASE_ASSET not in assets or "SHA256SUMS" not in assets:
        raise OSError("the latest release has no installable files")
    return {"version": tag.lstrip("v"), "url": assets[RELEASE_ASSET], "notes": (data.get("body") or "")[:2000]}


def start_command(version: str, release_root: str = "/opt/inkstant/current") -> list[str]:
    """The transient unit that runs the update, so it survives the camera app stopping."""
    return ["systemd-run", "--unit=inkstant-update", "--collect", "-p", "RuntimeMaxSec=1800",
            f"{release_root}/install/update.sh", version]
