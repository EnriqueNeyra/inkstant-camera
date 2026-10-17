#!/usr/bin/python3
"""Inkstant setup, the Python half (SPEC.md §15). Standard library only.

install.sh runs the parts that need apt and the network; this writes every file, and is
safe to run again: each step compares what is there with what should be, and changes only
the difference. `--root DIR` prefixes every path, so tests can run it against a fake system.

    configure.py preflight   [--force-model]
    configure.py questions   [--yes] [--reconfigure] [answer flags]
    configure.py apply       --release DIR [--update]
    configure.py pisugar     (settings over pisugar-server's socket, then internet time)
    configure.py summary
    configure.py uninstall   [--purge]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE_SRC = HERE.parent / "src"
sys.path.insert(0, str(RELEASE_SRC))

from inkstant import device as devmod  # noqa: E402
from inkstant import settings as setmod  # noqa: E402
from inkstant.atomic import atomic_write, write_json  # noqa: E402

ROOT = Path("/")
APT_PACKAGES = [
    "python3-picamera2", "python3-pil", "python3-numpy", "python3-gpiozero", "python3-lgpio",
    "python3-spidev", "python3-smbus2", "python3-flask", "python3-waitress", "python3-qrcode",
    "python3-piexif", "fonts-dejavu-core", "i2c-tools", "util-linux-extra",
]
SUPPORTED = {"pi3", "pi4", "pi5"}
WEB_USER = "inkstant-web"
CTL = "/usr/local/bin/inkstant-ctl"
BEGIN, END = "# BEGIN inkstant", "# END inkstant"
CONFLICTING = ["camera_auto_detect=1", "display_auto_detect=1", "dtoverlay=vc4-kms-v3d",
               "max_framebuffers=2", "dtparam=audio=on"]
COMMENT = "#inkstant: "
CMDLINE_ADD = ["quiet"]  # §13.2
DISABLE_UNITS = [
    "apt-daily.timer", "apt-daily-upgrade.timer", "man-db.timer", "e2scrub_all.timer",
    "dpkg-db-backup.timer", "fstrim.timer", "NetworkManager-wait-online.service",
    "systemd-timesyncd.service", "bluetooth.service", "hciuart.service", "ModemManager.service",
    "rpi-eeprom-update.service",
]
NO_LOOKALIKES = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def P(path: str) -> Path:
    """A system path under --root."""
    return ROOT / path.lstrip("/")


def say(msg: str) -> None:
    print(msg, flush=True)


def ok(step: str) -> None:
    say(f"  ok       {step}")


def changed(step: str) -> None:
    say(f"  changed  {step}")


class Stop(Exception):
    """Stop the install with a message: what's wrong and how to fix it."""


def run(*args: str, check: bool = True, timeout: float = 120, **kw) -> subprocess.CompletedProcess:
    if Path("/") != ROOT:
        # Under --root (tests) never touch the real system.
        return subprocess.CompletedProcess(args, 0, "", "")
    return subprocess.run(list(args), check=check, text=True, capture_output=True, timeout=timeout, **kw)


# Install state ---------------------------------------------------------------------------------
def state_path() -> Path:
    return P("/var/lib/inkstant/install-state.json")


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {"answers": {}, "disabled_units": [], "commented_lines": [], "cmdline_tokens": [], "backups": []}


def save_state(st: dict) -> None:
    st["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    state_path().parent.mkdir(parents=True, exist_ok=True)
    write_json(state_path(), st, mode=0o600, keep_backup=False)


def write_boot_file(path: Path, data: bytes) -> None:
    """Write a file on the FAT boot partition: no chmod or chown there (FAT has no permissions)."""
    tmp = path.with_name(path.name + ".inkstant-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_if_changed(path: Path, text: str, mode: int = 0o644, what: str | None = None) -> bool:
    data = text.encode()
    try:
        if path.read_bytes() == data and (path.stat().st_mode & 0o777) == mode:
            ok(what or str(path))
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, data, mode=mode)
    changed(what or str(path))
    return True


# Model ---------------------------------------------------------------------------------------------
def board_model() -> str:
    for p in (P("/proc/device-tree/model"), P("/sys/firmware/devicetree/base/model")):
        with contextlib.suppress(OSError):
            return p.read_text().rstrip("\x00\n")
    return "unknown"


def board_serial() -> str:
    for p in (P("/proc/device-tree/serial-number"), P("/sys/firmware/devicetree/base/serial-number")):
        with contextlib.suppress(OSError):
            return p.read_text().rstrip("\x00\n")
    with contextlib.suppress(OSError):
        for line in P("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("serial"):
                return line.split(":", 1)[1].strip()
    return secrets.token_hex(4)


# Preflight (§15.6 step 2) -----------------------------------------------------------------------------
def cmd_preflight(a) -> int:
    problems = []
    if os.geteuid() != 0 and Path("/") == ROOT:
        problems.append("Run it as root: sudo ./inkstant/install/install.sh")
    if not P("/etc/rpi-issue").exists():
        problems.append("This doesn't look like Raspberry Pi OS (no /etc/rpi-issue).")
    osr = ""
    with contextlib.suppress(OSError):
        osr = P("/etc/os-release").read_text()
    if "VERSION_CODENAME=trixie" not in osr:
        problems.append("Raspberry Pi OS Trixie is needed (flash the current Raspberry Pi OS Lite, 64-bit).")
    arch = run("dpkg", "--print-architecture", check=False).stdout.strip() if Path("/") == ROOT else "arm64"
    if arch and arch != "arm64":
        problems.append(f"The 64-bit OS is needed (this is {arch}).")
    model = board_model()
    fam = devmod.family_from_model(model)
    if fam is None and not a.force_model:
        problems.append(f"{model} isn't supported: use a Raspberry Pi 3A+, 3B, 3B+, 4B or 5 (see the README)."
                        " Use --force-model to try anyway.")
    if Path("/") == ROOT and shutil.which("cloud-init") and not P("/etc/cloud/cloud-init.disabled").exists():
        say("  waiting for first-boot setup (cloud-init) to finish …")
        run("cloud-init", "status", "--wait", check=False, timeout=300)
    st = os.statvfs(ROOT)
    if st.f_bavail * st.f_frsize < 2e9:
        problems.append("At least 2 GB must be free on the SD card.")
    from inkstant import version

    if time.time() < version.BUILD_EPOCH - 86400:
        problems.append("The clock is wrong, so downloads would fail. Wait a minute for network time, then run again.")
    if Path("/") == ROOT:
        for url in ("https://archive.raspberrypi.com/", "https://github.com/"):
            if run("curl", "-fsSI", "--max-time", "15", url, check=False).returncode != 0:
                problems.append(f"No internet: can't reach {url}. Check the Wi-Fi or Ethernet, then run again.")
                break
    if problems:
        for p in problems:
            say(f"  STOP: {p}")
        return 1
    say(f"  ok       {model} ({fam or 'forced'}), Raspberry Pi OS Trixie arm64")
    return 0


# Questions (§15.5) -----------------------------------------------------------------------------------------
def _tty():
    """The terminal, for questions: stdout goes to the install log, and answers mustn't."""
    try:
        return open("/dev/tty", "r+")  # noqa: SIM115
    except OSError:
        return None


def _prompt(text: str) -> str | None:
    tty = _tty()
    if tty is None:
        return None
    with tty:
        tty.write(text)
        tty.flush()
        line = tty.readline()
    return None if line == "" else line.strip()


def _prompt_note(text: str) -> None:
    tty = _tty()
    if tty is not None:
        with tty:
            tty.write(text)


def ask(prompt: str, default: str, yes: bool, check=None) -> str:
    if yes:
        return default
    while True:
        ans = _prompt(f"{prompt} [{default}]: ")
        if ans is None:
            return default
        value = ans or default
        if check is None:
            return value
        err = check(value)
        if not err:
            return value
        _prompt_note(f"    {err}\n")


def ask_yes(prompt: str, default: bool, yes: bool) -> bool:
    d = "Y/n" if default else "y/N"
    if yes:
        return default
    ans = _prompt(f"{prompt} [{d}]: ")
    if not ans:
        return default
    return ans.lower().startswith("y")


def _check(fn):
    def check(v):
        try:
            fn(v)
            return None
        except setmod.SettingsError as e:
            return e.message
    return check


def current_home_network() -> dict | None:
    """The Wi-Fi network this Pi is on now (set up in Imager), with its secret."""
    if Path("/") != ROOT or not shutil.which("nmcli"):
        return None
    out = run("nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active", check=False).stdout
    from inkstant.hw.network import split_terse

    for line in out.splitlines():
        f = split_terse(line)
        if len(f) >= 3 and f[1] == "802-11-wireless" and f[2] == "wlan0" and not f[0].startswith("inkstant-"):
            g = run("nmcli", "-s", "-g", "802-11-wireless.ssid,802-11-wireless-security.key-mgmt,"
                    "802-11-wireless-security.psk", "connection", "show", f[0], check=False).stdout.splitlines()
            g = [v.replace("\\:", ":").replace("\\\\", "\\") for v in g]
            if g and g[0]:
                return {"ssid": g[0], "key_mgmt": g[1] if len(g) > 1 else "", "psk": g[2] if len(g) > 2 else "",
                        "profile": f[0]}
    return None


def cmd_questions(a) -> int:
    st = load_state()
    ans = st.setdefault("answers", {})
    first = not ans
    if a.update and not first:
        ok("questions (keeping earlier answers)")
        return 0
    yes = a.yes or (a.update and first)
    model = board_model()
    fam = devmod.family_from_model(model) or "pi3"
    serial = board_serial()
    # Camera Wi-Fi
    default_ssid = ans.get("hotspot_ssid") or f"Inkstant-{serial[-4:].upper()}"
    old = setmod.load(P("/var/lib/inkstant/settings.json"))
    has_settings = P("/var/lib/inkstant/settings.json").exists()
    default_pw = (old["hotspot"]["password"] if has_settings else
                  "".join(secrets.choice(NO_LOOKALIKES) for _ in range(10)))
    if has_settings:
        default_ssid = old["hotspot"]["ssid"]
    ssid = a.hotspot_ssid or ask("Camera Wi-Fi name", default_ssid, yes, _check(setmod.validate_ssid))
    pw = a.hotspot_password or ask("Camera Wi-Fi password (8 to 63 characters)", default_pw, yes,
                                   _check(setmod.validate_wpa_password))
    setmod.validate_ssid(ssid)
    setmod.validate_wpa_password(pw)
    ans["hotspot_ssid"] = ssid
    st["hotspot_password_set"] = True
    st.setdefault("secrets", {})["hotspot_password"] = pw  # moved to settings.json by `apply`, then removed
    # Home network
    if a.no_home:
        ans["home"] = None
    elif a.home_ssid:
        ans["home"] = {"ssid": a.home_ssid}
        st["secrets"]["home_password"] = a.home_password or ""
    elif "home" not in ans or a.reconfigure:
        cur = current_home_network()
        if cur and ask_yes(f'Use "{cur["ssid"]}" for sharing at home?', True, yes):
            ans["home"] = {"ssid": cur["ssid"], "key_mgmt": cur["key_mgmt"]}
            st["secrets"]["home_password"] = cur["psk"]
        elif cur is None:
            say("  No home Wi-Fi found (installed over Ethernet?). Sharing will use the camera's Wi-Fi;"
                " add a home network later in Settings.")
            ans["home"] = None
        else:
            ans["home"] = None
    # Wi-Fi country
    if Path("/") == ROOT and shutil.which("raspi-config"):
        country = run("raspi-config", "nonint", "get_wifi_country", check=False).stdout.strip()
        if not country or a.country:
            c = a.country or ask("Wi-Fi country (two letters, e.g. US, GB, DE)", "US", yes,
                                 lambda v: None if re.fullmatch(r"[A-Za-z]{2}", v) else "Two letters, please.")
            ans["country"] = c.upper()
    # Mounting
    if a.camera_rotate is not None:
        ans["camera_rotate"] = a.camera_rotate
    elif "camera_rotate" not in ans or a.reconfigure:
        ans["camera_rotate"] = 180 if ask_yes("Is the camera mounted upside down?", False, yes) else 0
    if a.display_rotation is not None:
        ans["display_rotation"] = a.display_rotation
    elif "display_rotation" not in ans or a.reconfigure:
        ans["display_rotation"] = 180 if ask_yes("Is the display module mounted upside down?", False, yes) else 0
    if fam == "pi5":
        if a.camera_port:
            ans["camera_port"] = a.camera_port
        elif "camera_port" not in ans or a.reconfigure:
            port = ask("Pi 5: which camera connector is the ribbon in, CAM/DISP 0 or 1?", "1", yes,
                       lambda v: None if v in ("0", "1") else "Type 0 or 1.")
            ans["camera_port"] = f"cam{port}"
    if a.dev:
        ans["dev_mode"] = True
    elif "dev_mode" not in ans or a.reconfigure:
        ans["dev_mode"] = ask_yes("Developer mode: join home Wi-Fi at every start so SSH keeps working"
                                  " (uses more battery; change later in Settings)?", False, yes)
    ans["model"] = model
    ans["family"] = fam
    save_state(st)
    ok("answers saved")
    return 0


# Apply (§15.6 steps 8 to 12) -------------------------------------------------------------------------------
def release_version(release: Path) -> str:
    try:
        return (release / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0"


def build_epoch(release: Path) -> int:
    """For a git checkout: the last commit's time. Earlier than the install, so the RTC check is lenient."""
    with contextlib.suppress(Exception):
        out = subprocess.run(["git", "-C", str(release), "log", "-1", "--format=%ct"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
        if out:
            return int(out)
    from inkstant import version

    return version.BUILD_EPOCH


IGNORE = shutil.ignore_patterns(".git", ".github", ".venv", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache",
                                "sim", "tests", "mockups", "*.md", ".claude", "dist", "build")


def step_user() -> None:
    if Path("/") != ROOT:
        return
    if run("getent", "passwd", WEB_USER, check=False).returncode != 0:
        run("useradd", "--system", "--user-group", "--no-create-home", "--shell", "/usr/sbin/nologin", WEB_USER)
        changed(f"user {WEB_USER}")
    else:
        ok(f"user {WEB_USER}")
    groups = run("id", "-nG", WEB_USER, check=False).stdout.split()
    if "systemd-journal" not in groups:
        run("usermod", "-aG", "systemd-journal", WEB_USER, check=False)
        changed(f"{WEB_USER} can read the logs")


def _chown(path: Path, group: str | None) -> None:
    if Path("/") != ROOT:
        return
    import grp

    gid = grp.getgrnam(group).gr_gid if group else 0
    os.chown(path, 0, gid)


def step_folders() -> None:
    base = P("/var/lib/inkstant")
    spec = [(base, 0o755, None), (base / "photos", 0o750, WEB_USER), (base / "thumbs", 0o750, WEB_USER),
            (base / "epd", 0o700, None), (base / "tmp", 0o700, None), (base / "backup", 0o700, None)]
    for path, mode, group in spec:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, mode)
        _chown(path, group)
    # Files written before the group existed (re-runs) get the right group too.
    for folder in ("photos", "thumbs"):
        for f in (base / folder).iterdir():
            with contextlib.suppress(OSError):
                os.chmod(f, 0o640)
                _chown(f, WEB_USER)
    ok("folders /var/lib/inkstant")


def step_release(release: Path, update: bool) -> Path:
    version = release_version(release)
    releases = P("/opt/inkstant/releases")
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / version
    tmp = releases / f".{version}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(release, tmp, ignore=IGNORE, symlinks=True)
    build = tmp / "src/inkstant/_build.py"
    if not build.exists():
        build.write_text(f"VERSION = {version!r}\nBUILD_EPOCH = {build_epoch(release)}\n")
    if Path("/") == ROOT:
        run("python3", "-m", "compileall", "-q", str(tmp / "src"), check=False, timeout=600)
    if target.exists():
        old = releases / f".{version}.old"
        if old.exists():
            shutil.rmtree(old)
        os.rename(target, old)
        os.rename(tmp, target)
        shutil.rmtree(old)
    else:
        os.rename(tmp, target)
    current = P("/opt/inkstant/current")
    link_tmp = P("/opt/inkstant/current.tmp")
    if link_tmp.is_symlink() or link_tmp.exists():
        link_tmp.unlink()
    link_tmp.symlink_to(Path("releases") / version)
    os.replace(link_tmp, current)
    ctl = P(CTL)
    ctl.parent.mkdir(parents=True, exist_ok=True)
    if not ctl.is_symlink() or os.readlink(ctl) != "/opt/inkstant/current/bin/inkstant-ctl":
        if ctl.is_symlink() or ctl.exists():
            ctl.unlink()
        ctl.symlink_to("/opt/inkstant/current/bin/inkstant-ctl")
    # Keep the current and the previous release; delete older ones.
    others = sorted((p for p in releases.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != version),
                    key=lambda p: p.stat().st_mtime)
    for old in others[:-1]:
        shutil.rmtree(old, ignore_errors=True)
    changed(f"release {version} in /opt/inkstant (current)")
    return target


def step_device(ans: dict) -> None:
    d = devmod.Device(
        model=ans.get("model", "unknown"),
        family=ans.get("family", "pi3"),
        wifi_5ghz="Raspberry Pi 3 Model B Rev" not in ans.get("model", ""),  # the 3B is 2.4 GHz only
        camera_hflip=ans.get("camera_rotate", 0) == 180,
        camera_vflip=ans.get("camera_rotate", 0) == 180,
        display_rotation=ans.get("display_rotation", 0),
        camera_port=ans.get("camera_port", "cam1"),
    )
    path = P("/etc/inkstant/device.toml")
    if path.exists():
        existing = devmod.load(path)
        d = devmod.Device(model=d.model, family=d.family, wifi_5ghz=d.wifi_5ghz,
                          camera_hflip=d.camera_hflip, camera_vflip=d.camera_vflip,
                          display_rotation=d.display_rotation, camera_port=d.camera_port,
                          camera_fps=existing.camera_fps)
    write_if_changed(path, devmod.dumps(d), 0o644, "device.toml")


def step_settings(st: dict) -> None:
    path = P("/var/lib/inkstant/settings.json")
    s = setmod.load(path)
    ans = st["answers"]
    pw = st.get("secrets", {}).get("hotspot_password")
    s["hotspot"]["ssid"] = ans["hotspot_ssid"]
    if pw:
        s["hotspot"]["password"] = pw
    if ans.get("dev_mode"):
        s["dev_mode"] = True
    setmod.save(path, s)
    ok("settings.json")


def config_txt_block(family: str, camera_port: str) -> str:
    pi5_cam = "dtoverlay=imx708,cam0" if camera_port == "cam0" else "dtoverlay=imx708"
    return f"""{BEGIN} (managed by install/configure.py; edits here are overwritten)
[all]
dtparam=i2c_arm=on
dtparam=spi=on
dtparam=audio=off
camera_auto_detect=0
display_auto_detect=0
dtoverlay=vc4-kms-v3d,nohdmi,cma-128
max_framebuffers=1
disable_splash=1
force_eeprom_read=0
disable_poe_fan=1
ignore_lcd=1
disable_touchscreen=1
disable_camera_led=1
dtparam=act_led_trigger=none
dtparam=pwr_led_trigger=none
dtoverlay=disable-bt
gpio=26=op,dh
dtoverlay=gpio-led,gpio=26,label=status,trigger=timer
[pi3]
dtoverlay=imx708
dtoverlay=i2c-rtc,ds3231
[pi4]
dtoverlay=imx708
dtoverlay=i2c-rtc,ds3231
[pi5]
{pi5_cam}
[all]
{END}
"""


def edit_config_txt(text: str, family: str, camera_port: str) -> tuple[str, list[str]]:
    """Returns (new text, stock lines commented out). Pure: tested against the stock file."""
    lines = text.splitlines()
    out, inside, commented = [], False, []
    for line in lines:
        if line.startswith(BEGIN):
            inside = True
            continue
        if inside:
            if line.startswith(END):
                inside = False
            continue
        if line.strip() in CONFLICTING:
            commented.append(line.strip())
            line = COMMENT + line
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    body = "\n".join(out) + "\n\n" + config_txt_block(family, camera_port)
    # Checks (§15.6): no active vc4-kms-v3d outside the block, exactly one block.
    outside = body.split(BEGIN)[0]
    if any(ln.strip().startswith("dtoverlay=vc4-kms-v3d") for ln in outside.splitlines()):
        raise Stop("config.txt still has an active vc4-kms-v3d line outside the Inkstant block.")
    if body.count(BEGIN) != 1 or not body.endswith("\n"):
        raise Stop("config.txt check failed.")
    return body, commented


def unedit_config_txt(text: str) -> str:
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith(BEGIN):
            inside = True
            continue
        if inside:
            if line.startswith(END):
                inside = False
            continue
        if line.startswith(COMMENT):
            line = line[len(COMMENT):]
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def boot_dir() -> Path:
    return P("/boot/firmware") if P("/boot/firmware/config.txt").exists() else P("/boot")


def step_config_txt(st: dict) -> None:
    path = boot_dir() / "config.txt"
    text = path.read_text()
    bak = path.with_name("config.txt.inkstant-bak")
    if not bak.exists():
        shutil.copyfile(path, bak)  # data only: FAT can't take Unix permissions
        st.setdefault("backups", []).append(str(bak))
    new, commented = edit_config_txt(text, st["answers"].get("family", "pi3"), st["answers"].get("camera_port", "cam1"))
    for c in commented:
        if c not in st.setdefault("commented_lines", []):
            st["commented_lines"].append(c)
    if new != text:
        write_boot_file(path, new.encode())
        changed("config.txt (takes effect after the reboot)")
    else:
        ok("config.txt")


def edit_cmdline(text: str) -> tuple[str, list[str]]:
    """Returns (new text, tokens added). cmdline.txt is a single line; nothing else changes (§13.2)."""
    line = text.strip()
    tokens = line.split()
    added = [t for t in CMDLINE_ADD if t not in tokens]
    if not added:
        return text, []
    return " ".join([line, *added]) + "\n", added


def unedit_cmdline(text: str, added: list[str]) -> str:
    tokens = text.strip().split(" ")
    for t in added:
        if t in tokens:
            tokens.reverse()
            tokens.remove(t)  # the last one: ours were appended
            tokens.reverse()
    return " ".join(tokens) + "\n"


def step_cmdline(st: dict) -> None:
    path = boot_dir() / "cmdline.txt"
    if not path.exists():
        return
    text = path.read_text()
    bak = path.with_name("cmdline.txt.inkstant-bak")
    if not bak.exists():
        shutil.copyfile(path, bak)
        st.setdefault("backups", []).append(str(bak))
    new, added = edit_cmdline(text)
    for t in added:
        if t not in st.setdefault("cmdline_tokens", []):
            st["cmdline_tokens"].append(t)
    if new != text:
        write_boot_file(path, new.encode())
        changed("cmdline.txt (takes effect after the reboot)")
    else:
        ok("cmdline.txt")


def step_system_files(release_root: Path) -> None:
    sysdir = release_root / "system"
    mapping = [
        ("systemd/inkstant.service", "/etc/systemd/system/inkstant.service", 0o644),
        ("systemd/inkstant-gallery.service", "/etc/systemd/system/inkstant-gallery.service", 0o644),
        ("systemd/inkstant-failsafe.service", "/etc/systemd/system/inkstant-failsafe.service", 0o644),
        ("systemd/pisugar-server.service.d/inkstant.conf", "/etc/systemd/system/pisugar-server.service.d/inkstant.conf", 0o644),
        ("system-shutdown/inkstant-poweroff", "/usr/lib/systemd/system-shutdown/inkstant-poweroff", 0o755),
        ("journald.conf.d/inkstant.conf", "/etc/systemd/journald.conf.d/inkstant.conf", 0o644),
        ("system.conf.d/inkstant.conf", "/etc/systemd/system.conf.d/inkstant.conf", 0o644),
        ("modules-load.d/inkstant.conf", "/etc/modules-load.d/inkstant.conf", 0o644),
        ("NetworkManager/dnsmasq-shared.d/inkstant.conf", "/etc/NetworkManager/dnsmasq-shared.d/inkstant.conf", 0o644),
    ]
    for src, dst, mode in mapping:
        write_if_changed(P(dst), (sysdir / src).read_text(), mode, dst)
    cloud = P("/etc/cloud/cloud-init.disabled")
    if P("/etc/cloud").exists() and not cloud.exists():
        cloud.write_text("Disabled by Inkstant: first-boot setup is done (SPEC.md §13.6).\n")
        changed("cloud-init disabled")


def step_units(st: dict, update: bool) -> None:
    if Path("/") != ROOT:
        return
    run("systemctl", "daemon-reload", check=False)
    run("systemctl", "enable", "inkstant.service", check=False)
    ok("inkstant.service enabled")
    for unit in DISABLE_UNITS:
        r = run("systemctl", "is-enabled", unit, check=False)
        if r.stdout.strip() == "enabled":
            if unit not in st.setdefault("disabled_units", []):
                st["disabled_units"].append(unit)
            # During an update nothing is stopped: the camera is in use until its own restart.
            run("systemctl", "disable", *([] if update else ["--now"]), unit, check=False)
            changed(f"disabled {unit}")


def step_wifi(st: dict) -> None:
    from inkstant.hw.network import Network, home_args, hotspot_args

    conn_dir = P("/etc/NetworkManager/system-connections")
    net = Network(conn_dir=conn_dir, run=(subprocess.run if Path("/") == ROOT else _fake_nmcli))
    s = setmod.load(P("/var/lib/inkstant/settings.json"))
    net.write_profile("inkstant-hotspot", net.keyfile(hotspot_args(s["hotspot"]["ssid"], s["hotspot"]["password"])))
    changed("Wi-Fi profile inkstant-hotspot")
    ans = st["answers"]
    home = ans.get("home")
    secret = st.get("secrets", {}).get("home_password")
    if home and secret is not None:
        homes = net.homes()
        n = next((h.n for h in homes if h.ssid == home["ssid"]), None) or net.free_home_slot() or 1
        key_mgmt = "sae" if home.get("key_mgmt") == "sae" else "wpa-psk"
        net.write_profile(f"inkstant-home-{n}", net.keyfile(home_args(n, home["ssid"], secret or None,
                                                                      autoconnect=bool(ans.get("dev_mode")) and n == 1,
                                                                      key_mgmt=key_mgmt)))
        changed(f"Wi-Fi profile inkstant-home-{n} ({home['ssid']})")
    elif home:
        # A re-run keeps the saved profile and its password; only developer mode may have changed.
        n = next((h.n for h in net.homes() if h.ssid == home["ssid"]), None)
        if n == 1:
            net.set_home_autoconnect(1, bool(ans.get("dev_mode")))
            ok(f"Wi-Fi profile inkstant-home-1 joins at every start: {'yes' if ans.get('dev_mode') else 'no'}")
    # Retire Imager's netplan profile; nothing disconnects until the reboot.
    backup = P("/var/lib/inkstant/backup/netplan")
    for y in sorted(P("/etc/netplan").glob("*.yaml")) if P("/etc/netplan").exists() else []:
        backup.mkdir(parents=True, exist_ok=True)
        shutil.move(str(y), backup / y.name)
        st.setdefault("netplan_moved", []).append(y.name)
        changed(f"netplan {y.name} moved to {backup}")


def _fake_nmcli(args, **kw):
    """Tests: `nmcli --offline connection add …` prints a keyfile."""
    if "--offline" in args:
        a = args[args.index("add") + 1:]
        kv = dict(zip(a[0::2], a[1::2], strict=False))
        text = f"[connection]\nid={kv.get('con-name')}\ntype=wifi\n[wifi]\nssid={kv.get('ssid')}\n"
        return subprocess.CompletedProcess(args, 0, text, "")
    return subprocess.CompletedProcess(args, 0, "", "")


def cmd_apply(a) -> int:
    st = load_state()
    if not st.get("answers"):
        raise Stop("No answers yet: run the questions step first.")
    release = Path(a.release).resolve()
    say("Step 8: app files")
    step_user()
    step_folders()
    target = step_release(release, a.update)
    say("Step 9: camera settings files")
    step_device(st["answers"])
    step_settings(st)
    say("Step 10: Wi-Fi")
    step_wifi(st)
    say("Step 12: system files")
    step_config_txt(st)
    step_cmdline(st)
    step_system_files(target)
    step_units(st, a.update)
    # The hotspot and home passwords now live in settings.json and NetworkManager, not here.
    st.pop("secrets", None)
    st["version"] = release_version(release)
    st["installed"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_state(st)
    return 0


# PiSugar settings and clock (§15.6 step 11) ----------------------------------------------------------------
def cmd_pisugar(a) -> int:
    from inkstant.hw.pisugar import PiSugarClient, PiSugarError, ensure_settings

    async def main() -> int:
        client = PiSugarClient(P("/tmp/pisugar-server.sock"))
        if not await client.connect(timeout=10):
            say("  STOP: pisugar-server isn't answering on /tmp/pisugar-server.sock.")
            return 1
        try:
            problems = await ensure_settings(client, f"{CTL} power-button", say)
            if problems:
                for p in problems:
                    say(f"  STOP: PiSugar setting didn't stick: {p}")
                return 1
            ok("PiSugar settings (soft power-off, our command, no auto power-on, anti-mistouch, backup at 3%)")
            try:
                await client.rtc_web()
                await asyncio.sleep(5)
                rtc = await client.rtc_time()
                drift = abs(rtc.timestamp() - time.time())
                if drift <= 5:
                    ok(f"PiSugar clock set from internet time ({rtc.isoformat(timespec='seconds')})")
                else:
                    say(f"  warning  the PiSugar clock is {drift:.0f} s off; set it from a phone later")
            except PiSugarError as e:
                say(f"  warning  couldn't set the clock: {e}")
            return 0
        finally:
            await client.close()

    return asyncio.run(main())


# Summary (§15.6 step 14) ----------------------------------------------------------------------------------
def cmd_summary(a) -> int:
    s = setmod.load(P("/var/lib/inkstant/settings.json"))
    st = load_state()
    dev = st.get("answers", {}).get("dev_mode")
    # Straight to the terminal: the install log never gets the password (§15.4).
    try:
        tty = open("/dev/tty", "w")  # noqa: SIM115
    except OSError:
        tty = sys.stdout

    def say(msg: str) -> None:  # noqa: F811
        print(msg, file=tty, flush=True)

    say("")
    say("Inkstant is installed.")
    say(f"  Camera Wi-Fi:  {s['hotspot']['ssid']}")
    say(f"  Password:      {s['hotspot']['password']}")
    say("  To share: press the PiSugar's BTN. The display module shows a QR code for the gallery.")
    say("  The light: solid, then a slow blink while starting, then solid when ready (README: the status light).")
    if dev:
        say("  Developer mode is on: the camera joins your home Wi-Fi at every start, so SSH keeps working.")
    else:
        say("  After the reboot the camera turns its Wi-Fi off, so SSH stops working. SSH works while")
        say("  sharing at home, or turn on developer mode (Settings → Advanced, or --dev).")
    return 0


# Uninstall (§15.9) ----------------------------------------------------------------------------------------
def cmd_uninstall(a) -> int:
    st = load_state()
    if Path("/") == ROOT:
        for unit in ("inkstant.service", "inkstant-gallery.service"):
            run("systemctl", "disable", "--now", unit, check=False)
    for f in ("/etc/systemd/system/inkstant.service", "/etc/systemd/system/inkstant-gallery.service",
              "/etc/systemd/system/inkstant-failsafe.service",
              "/etc/systemd/system/pisugar-server.service.d/inkstant.conf",
              "/usr/lib/systemd/system-shutdown/inkstant-poweroff", "/etc/systemd/journald.conf.d/inkstant.conf",
              "/etc/systemd/system.conf.d/inkstant.conf", "/etc/modules-load.d/inkstant.conf",
              "/etc/NetworkManager/dnsmasq-shared.d/inkstant.conf", CTL, "/etc/inkstant/device.toml",
              "/etc/cloud/cloud-init.disabled"):
        p = P(f)
        if p.is_symlink() or p.exists():
            p.unlink()
            changed(f"removed {f}")
    for p in sorted(P("/etc/NetworkManager/system-connections").glob("inkstant-*.nmconnection")) \
            if P("/etc/NetworkManager/system-connections").exists() else []:
        p.unlink()
        changed(f"removed {p.name}")
    cfg = boot_dir() / "config.txt"
    if cfg.exists():
        write_boot_file(cfg, unedit_config_txt(cfg.read_text()).encode())
        changed("config.txt restored")
    cmdline = boot_dir() / "cmdline.txt"
    if cmdline.exists() and st.get("cmdline_tokens"):
        write_boot_file(cmdline, unedit_cmdline(cmdline.read_text(), st["cmdline_tokens"]).encode())
        changed("cmdline.txt restored")
    for name in st.get("netplan_moved", []):
        src = P("/var/lib/inkstant/backup/netplan") / name
        if src.exists() and (a.yes or ask_yes(f"Restore netplan {name} (Imager's Wi-Fi)?", True, False)):
            P("/etc/netplan").mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), P("/etc/netplan") / name)
            changed(f"restored netplan {name}")
    if Path("/") == ROOT:
        for unit in st.get("disabled_units", []):
            run("systemctl", "enable", unit, check=False)
            changed(f"re-enabled {unit}")
        run("systemctl", "daemon-reload", check=False)
    shutil.rmtree(P("/opt/inkstant"), ignore_errors=True)
    changed("removed /opt/inkstant")
    if a.purge:
        shutil.rmtree(P("/var/lib/inkstant"), ignore_errors=True)
        changed("removed /var/lib/inkstant (photos included)")
    else:
        for f in ("settings.json", "settings.json.bak", "state.json", "state.json.bak", "slideshow.json",
                  "selftest.json", "install-state.json"):
            with contextlib.suppress(FileNotFoundError):
                (P("/var/lib/inkstant") / f).unlink()
        say("  kept     /var/lib/inkstant/photos (use --purge to delete them)")
    if Path("/") == ROOT:
        run("userdel", WEB_USER, check=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    global ROOT
    p = argparse.ArgumentParser(prog="configure.py")
    p.add_argument("--root", default="/")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("preflight")
    pf.add_argument("--force-model", action="store_true")
    q = sub.add_parser("questions")
    q.add_argument("--yes", action="store_true")
    q.add_argument("--update", action="store_true")
    q.add_argument("--reconfigure", action="store_true")
    q.add_argument("--dev", action="store_true")
    q.add_argument("--hotspot-ssid")
    q.add_argument("--hotspot-password")
    q.add_argument("--home-ssid")
    q.add_argument("--home-password")
    q.add_argument("--no-home", action="store_true")
    q.add_argument("--country")
    q.add_argument("--camera-rotate", type=int, choices=[0, 180])
    q.add_argument("--display-rotation", type=int, choices=[0, 180])
    q.add_argument("--camera-port", choices=["cam0", "cam1"])
    ap = sub.add_parser("apply")
    ap.add_argument("--release", required=True)
    ap.add_argument("--update", action="store_true")
    sub.add_parser("pisugar")
    sub.add_parser("summary")
    un = sub.add_parser("uninstall")
    un.add_argument("--purge", action="store_true")
    un.add_argument("--yes", action="store_true")
    a = p.parse_args(argv)
    ROOT = Path(a.root)
    try:
        return {"preflight": cmd_preflight, "questions": cmd_questions, "apply": cmd_apply,
                "pisugar": cmd_pisugar, "summary": cmd_summary, "uninstall": cmd_uninstall}[a.cmd](a)
    except Stop as e:
        say(f"  STOP: {e}")
        return 1
    except setmod.SettingsError as e:
        say(f"  STOP: {e.message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
