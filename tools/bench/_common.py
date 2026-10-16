"""Shared helpers for the bench scripts (SPEC.md §19.5).

Run them on the Pi, as root, with the camera app stopped:

    sudo systemctl stop inkstant
    sudo python3 /opt/inkstant/current/tools/bench/<script>.py
    sudo systemctl start inkstant

Results go to /var/lib/inkstant/bench (or $INKSTANT_BENCH_DIR).
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def results_dir() -> Path:
    d = Path(os.environ.get("INKSTANT_BENCH_DIR", "/var/lib/inkstant/bench"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(name: str, data) -> Path:
    p = results_dir() / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(data, indent=2, default=str))
    print(f"\nRaw data saved to {p}")
    return p


def require_root() -> None:
    if os.geteuid() != 0:
        sys.exit("Run this as root: sudo python3 " + " ".join(sys.argv))


def require_app_stopped() -> None:
    """The camera app owns the camera, GPIO and the display; it has to be stopped first."""
    try:
        r = subprocess.run(["systemctl", "is-active", "inkstant.service"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return
    if r.stdout.strip() in ("active", "activating", "deactivating"):
        sys.exit("The camera app is running and owns the hardware. Stop it first:\n"
                 "    sudo systemctl stop inkstant\n"
                 "and start it again afterwards with: sudo systemctl start inkstant")


def meminfo() -> dict[str, int]:
    out = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            out[key] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return out


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    s = sorted(values)
    p90 = s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]
    return {"n": len(s), "min": round(s[0], 1), "median": round(statistics.median(s), 1),
            "p90": round(p90, 1), "max": round(s[-1], 1)}


def fmt_stats(st: dict, unit: str = "ms") -> str:
    if not st.get("n"):
        return "no samples"
    return (f"median {st['median']} {unit}, p90 {st['p90']} {unit}, "
            f"min {st['min']}, max {st['max']} (n={st['n']})")


def device():
    from inkstant import device as devmod
    from inkstant.paths import Paths

    return devmod.load(Paths.default().device_toml)
