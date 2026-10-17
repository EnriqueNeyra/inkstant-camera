"""The self-test / setup check (SPEC.md §15.8, §17). Standard library only.

Run by the camera app on its first start after an install or update (with the display
probe), by `inkstant-ctl selftest`, and by the install script before its reboot
(`install=True`: only what can be true before the reboot).
"""

from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from inkstant import version
from inkstant.paths import Paths

MIN_FIRMWARE = (1, 2, 4)
CTL_COMMAND = "/usr/local/bin/inkstant-ctl power-button"


@dataclass
class Check:
    name: str
    ok: bool
    critical: bool
    detail: str = ""
    hint: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text)
    return tuple(int(n) for n in nums[:3]) if nums else ()


def pisugar_sync_get(sock: Path, timeout: float = 2.0) -> Callable[[str], str]:
    """A minimal blocking pisugar-server client for tools (the app uses its async one)."""

    def get(name: str) -> str:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(sock))
            s.sendall(f"get {name}\n".encode())
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    raise OSError("connection closed")
                buf += chunk
                for line in buf.decode(errors="replace").splitlines():
                    if line.startswith(name + ":"):
                        return line[len(name) + 1:].strip()
                    if line and line not in ("single", "double", "long") and not line.startswith(name):
                        raise OSError(line)
    return get


def _cmd(*args: str, timeout: float = 10) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def check_pisugar(get: Callable[[str], str]) -> list[Check]:
    out = []
    try:
        model = get("model")
    except Exception as e:
        return [Check("PiSugar", False, True, f"pisugar-server doesn't answer: {e}",
                      "Check the PiSugar is attached and switched on, then restart the camera.")]
    out.append(Check("PiSugar model", model == "PiSugar 3", True, model,
                     "pisugar-server must be set up for a PiSugar 3: run the install script again."))
    try:
        fw = get("firmware_version")
        ok = parse_version(fw) >= MIN_FIRMWARE
        out.append(Check("PiSugar firmware", ok, True, fw,
                         "Firmware 1.2.4 or later is needed for the power-off; contact PiSugar support."))
    except Exception as e:
        out.append(Check("PiSugar firmware", False, True, str(e), ""))
    for name, want, hint in (
        ("soft_poweroff", "true", "The power button would cut power without a clean shutdown."),
        ("soft_poweroff_shell", CTL_COMMAND, "The power button wouldn't reach the camera app."),
        ("auto_power_on", "false", "Plugging in would turn the camera on."),
        ("anti_mistouch", "true", "A light touch could turn the camera on."),
    ):
        try:
            got = get(name)
            out.append(Check(f"PiSugar {name}", got == want, True, got, hint + " The camera app fixes this at start."))
        except Exception as e:
            out.append(Check(f"PiSugar {name}", False, True, str(e), hint))
    return out


def check_i2c_0x57() -> Check:
    out = _cmd("i2cget", "-y", "1", "0x57", "0x00")
    ok = out.strip().startswith("0x")
    return Check("PiSugar on I2C (0x57)", ok, True, out.strip() or "no answer",
                 "Reseat the PiSugar; its pogo pins must touch the Pi's I2C pins.")


def check_no_tcp() -> Check:
    out = _cmd("ss", "-ltnH")
    open_ports = {p for p in ("8421", "8422", "8423") if re.search(rf":{p}\b", out)}
    return Check("pisugar-server has no network ports", not open_ports, False,
                 ", ".join(sorted(open_ports)) or "none",
                 "Run the install script again to lock pisugar-server down.")


def check_buses(root: Path) -> list[Check]:
    return [
        Check("SPI bus", (root / "dev/spidev0.0").exists(), True, "/dev/spidev0.0",
              "SPI isn't on: run the install script again, then reboot."),
        Check("I2C bus", (root / "dev/i2c-1").exists(), True, "/dev/i2c-1",
              "I2C isn't on: run the install script again, then reboot."),
    ]


def check_rtc(root: Path, family: str) -> Check:
    if family == "pi5":
        return Check("Clock source", True, False, "Pi 5: read from the PiSugar at start")
    try:
        name = (root / "sys/class/rtc/rtc0/name").read_text().strip()
    except OSError:
        name = ""
    ok = "ds1307" in name or "ds3231" in name
    return Check("PiSugar clock driver", ok, False, name or "no rtc0",
                 "The kernel RTC overlay isn't loaded; the app falls back to pisugar-server.")


def check_clock_valid(epoch: int | None) -> Check:
    ok = epoch is not None and epoch >= version.BUILD_EPOCH
    return Check("Clock set", ok, False, "valid" if ok else "unset",
                 "Share at home, or use \"Use this phone's time\" in Settings.")


def check_led(root: Path) -> Check:
    path = root / "sys/class/leds/status"
    try:
        triggers = (path / "trigger").read_text()
    except OSError:
        return Check("Status light", False, False, "missing",
                     "The gpio-led overlay isn't loaded: run the install script again, then reboot.")
    ok = "pattern" in triggers
    return Check("Status light", ok, False, "pattern trigger" if ok else "no pattern trigger",
                 "ledtrig-pattern isn't loaded; the light still works, blinking from software.")


def check_cma(root: Path, family: str) -> Check:
    if family == "pi5":
        return Check("Camera memory (CMA)", True, False, "Pi 5: not used for the camera")
    try:
        meminfo = (root / "proc/meminfo").read_text()
        total = int(re.search(r"CmaTotal:\s+(\d+)", meminfo).group(1))
    except (OSError, AttributeError, ValueError):
        return Check("Camera memory (CMA)", False, False, "unknown", "")
    ok = total >= 120 * 1024
    return Check("Camera memory (CMA)", ok, True, f"{total // 1024} MB",
                 "config.txt should set cma-128: run the install script again, then reboot.")


def check_rpi_apps_yaml(root: Path, family: str) -> Check:
    if family == "pi5":
        return Check("Camera pipeline config", True, False, "Pi 5")
    path = root / "usr/share/libcamera/pipeline/rpi/vc4/rpi_apps.yaml"
    return Check("Camera pipeline config", path.exists(), True, str(path),
                 "Reinstall python3-picamera2; without this file the camera needs too much memory.")


def check_config_txt(root: Path) -> Check:
    for p in (root / "boot/firmware/config.txt", root / "boot/config.txt"):
        try:
            text = p.read_text()
        except OSError:
            continue
        ok = "# BEGIN inkstant" in text and "# END inkstant" in text
        return Check("config.txt", ok, True, str(p), "Run the install script again.")
    return Check("config.txt", False, True, "not found", "Run the install script again.")


def check_profiles(root: Path) -> Check:
    d = root / "etc/NetworkManager/system-connections"
    try:
        ok = (d / "inkstant-hotspot.nmconnection").exists()
    except OSError:
        ok = False
    return Check("Camera Wi-Fi profile", ok, True, "inkstant-hotspot", "Run the install script again.")


def check_storage(paths: Paths) -> list[Check]:
    out = []
    try:
        st = os.statvfs(paths.var)
        free = st.f_bavail * st.f_frsize
        out.append(Check("Free space", free >= (500 << 20), True, f"{free / 1e9:.1f} GB free",
                         "Delete photos to make room."))
    except OSError as e:
        out.append(Check("Free space", False, True, str(e), ""))
    for folder, mode in ((paths.photos, 0o750), (paths.thumbs, 0o750), (paths.epd, 0o700)):
        try:
            m = stat.S_IMODE(folder.stat().st_mode)
            out.append(Check(f"Folder {folder.name}", m == mode, False, oct(m), "Run the install script again."))
        except OSError:
            out.append(Check(f"Folder {folder.name}", False, True, "missing", "Run the install script again."))
    return out


def run(paths: Paths, *, family: str = "pi3", install: bool = False,
        pisugar_get: Callable[[str], str] | None = None,
        camera_present: Callable[[], bool] | None = None,
        display_probe: Callable[[], bool | None] | None = None,
        module_present: Callable[[], bool] | None = None,
        rtc_epoch: int | None = None) -> list[Check]:
    root = paths.root
    checks: list[Check] = []
    get = pisugar_get or pisugar_sync_get(paths.pisugar_sock)
    checks += check_pisugar(get)
    if root == Path("/"):
        checks.append(check_i2c_0x57())
        checks.append(check_no_tcp())
    checks += check_storage(paths)
    checks.append(check_profiles(root))
    checks.append(check_config_txt(root))
    checks.append(check_rpi_apps_yaml(root, family))
    if install:
        return checks
    checks += check_buses(root)
    checks.append(check_rtc(root, family))
    checks.append(check_clock_valid(rtc_epoch))
    checks.append(check_led(root))
    checks.append(check_cma(root, family))
    if camera_present is not None:
        try:
            ok = camera_present()
            checks.append(Check("Camera", ok, True, "found" if ok else "not found",
                                "Check the camera ribbon: contacts away from the latch, fully in, latch closed."))
        except Exception as e:
            checks.append(Check("Camera", False, True, str(e), "Check the camera ribbon."))
    if display_probe is not None:
        result = display_probe()
        if result is None:
            present = module_present is not None and module_present()
            checks.append(Check("Display module", True, False,
                                "module in, not tested while idle" if present else "no module in: not tested"))
        else:
            checks.append(Check("Display module", result, True, "answers" if result else "doesn't answer",
                                "Slide the module out and back in; check the connector's wiring."))
    return checks


def summary(checks: list[Check]) -> dict:
    failed = [c.name for c in checks if not c.ok and c.critical]
    return {"passed": not failed, "failed": failed, "checks": [asdict(c) for c in checks]}
