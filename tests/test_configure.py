"""install/configure.py against a fake system (SPEC.md §19.4)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONFIGURE = REPO / "install" / "configure.py"

# Raspberry Pi OS Lite (Trixie) stock config.txt.
STOCK_CONFIG = """# For more options and information see
# http://rptl.io/configtxt
# Some settings may impact device functionality. See link above for details

# Uncomment some or all of these to enable the optional hardware interfaces
#dtparam=i2c_arm=on
#dtparam=i2s=on
#dtparam=spi=on

# Enable audio (loads snd_bcm2835)
dtparam=audio=on

# Additional overlays and parameters are documented
# /boot/firmware/overlays/README

# Automatically load overlays for detected cameras
camera_auto_detect=1

# Automatically load overlays for detected DSI displays
display_auto_detect=1

# Automatically load initramfs files, if found
auto_initramfs=1

# Enable DRM VC4 V3D driver
dtoverlay=vc4-kms-v3d
max_framebuffers=2

# Don't have the firmware create an initial video= setting in cmdline.txt.
# Use the kernel's default instead.
disable_fw_kms_setup=1

# Run in 64-bit mode
arm_64bit=1

# Disable compensation for displays with overscan
disable_overscan=1

# Run as fast as firmware / board allows
arm_boost=1

[cm4]
# Enable host mode on the 2711 built-in XHCI USB controller.
# This line should be removed if the legacy DWC2 controller is required
# (e.g. for USB device mode) or if USB support is not required.
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[all]
"""

STOCK_CMDLINE = ("console=serial0,115200 console=tty1 root=PARTUUID=4e639091-02 rootfstype=ext4 fsck.repair=yes "
                 "rootwait cfg80211.ieee80211_regdom=US\n")


def fake_root(tmp_path: Path, model="Raspberry Pi 3 Model A Plus Rev 1.1") -> Path:
    r = tmp_path / "root"
    (r / "etc/netplan").mkdir(parents=True)
    (r / "etc/rpi-issue").write_text("Raspberry Pi reference 2026-10-06\n")
    (r / "etc/os-release").write_text('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nVERSION_CODENAME=trixie\n')
    (r / "proc/device-tree").mkdir(parents=True)
    (r / "proc/device-tree/model").write_text(model + "\x00")
    (r / "proc/device-tree/serial-number").write_text("00000000c0ffee4f2a\x00")
    (r / "boot/firmware").mkdir(parents=True)
    (r / "boot/firmware/config.txt").write_text(STOCK_CONFIG)
    (r / "boot/firmware/cmdline.txt").write_text(STOCK_CMDLINE)
    (r / "etc/netplan/50-cloud-init.yaml").write_text("network: {version: 2}\n")
    (r / "etc/cloud").mkdir(parents=True)
    (r / "etc/NetworkManager/system-connections").mkdir(parents=True)
    return r


def cfg(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    return subprocess.run([sys.executable, str(CONFIGURE), "--root", str(root), *args],
                          capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)


@pytest.fixture
def installed(tmp_path):
    r = fake_root(tmp_path)
    q = cfg(r, "questions", "--yes", "--hotspot-ssid", "Inkstant-TEST", "--hotspot-password", "secretpass1",
            "--no-home", "--camera-rotate", "180")
    assert q.returncode == 0, q.stdout + q.stderr
    a = cfg(r, "apply", "--release", str(REPO))
    assert a.returncode == 0, a.stdout + a.stderr
    return r


def test_preflight(tmp_path):
    r = fake_root(tmp_path)
    p = cfg(r, "preflight")
    assert p.returncode == 0, p.stdout
    r2 = fake_root(tmp_path / "zero", model="Raspberry Pi Zero 2 W Rev 1.0")
    p = cfg(r2, "preflight")
    assert p.returncode == 1 and "isn't supported" in p.stdout
    assert cfg(r2, "preflight", "--force-model").returncode == 0


def test_apply_writes_everything(installed):
    r = installed
    cur = r / "opt/inkstant/current"
    assert cur.is_symlink() and os.readlink(cur) == "releases/1.0.0"
    rel = r / "opt/inkstant/releases/1.0.0"
    assert (rel / "src/inkstant/camera_app/app.py").exists() and (rel / "bin/inkstant-ctl").exists()
    assert not (rel / "tests").exists() and not (rel / ".git").exists()
    assert (rel / "LICENSE").exists()  # the license travels with every copy
    assert "BUILD_EPOCH" in (rel / "src/inkstant/_build.py").read_text()
    assert os.readlink(r / "usr/local/bin/inkstant-ctl") == "/opt/inkstant/current/bin/inkstant-ctl"
    dev = (r / "etc/inkstant/device.toml").read_text()
    assert "camera_hflip = true" in dev and "camera_vflip = true" in dev and 'family = "pi3"' in dev
    s = json.loads((r / "var/lib/inkstant/settings.json").read_text())
    assert s["hotspot"] == {"ssid": "Inkstant-TEST", "password": "secretpass1"}
    st = json.loads((r / "var/lib/inkstant/install-state.json").read_text())
    assert "secrets" not in st  # passwords moved to settings.json / NetworkManager
    cfgtxt = (r / "boot/firmware/config.txt").read_text()
    assert cfgtxt.count("# BEGIN inkstant") == 1 and "#inkstant: dtoverlay=vc4-kms-v3d" in cfgtxt
    assert "#inkstant: camera_auto_detect=1" in cfgtxt and "#inkstant: dtparam=audio=on" in cfgtxt
    assert "dtoverlay=gpio-led,gpio=26,label=status,trigger=timer" in cfgtxt
    assert (r / "boot/firmware/config.txt.inkstant-bak").read_text() == STOCK_CONFIG
    assert (r / "boot/firmware/cmdline.txt").read_text() == STOCK_CMDLINE.strip() + " quiet\n"
    hook = r / "usr/lib/systemd/system-shutdown/inkstant-poweroff"
    assert hook.exists() and (hook.stat().st_mode & 0o777) == 0o755
    for f in ("etc/systemd/system/inkstant.service", "etc/systemd/system/inkstant-gallery.service",
              "etc/systemd/system/pisugar-server.service.d/inkstant.conf", "etc/modules-load.d/inkstant.conf",
              "etc/NetworkManager/dnsmasq-shared.d/inkstant.conf", "etc/cloud/cloud-init.disabled"):
        assert (r / f).exists(), f
    assert not list((r / "etc/netplan").glob("*.yaml"))
    assert (r / "var/lib/inkstant/backup/netplan/50-cloud-init.yaml").exists()
    hs = r / "etc/NetworkManager/system-connections/inkstant-hotspot.nmconnection"
    assert hs.exists() and (hs.stat().st_mode & 0o777) == 0o600
    for folder, mode in (("photos", 0o750), ("thumbs", 0o750), ("epd", 0o700)):
        assert ((r / "var/lib/inkstant" / folder).stat().st_mode & 0o777) == mode


def test_second_run_changes_nothing_that_matters(installed):
    r = installed
    before = (r / "boot/firmware/config.txt").read_text()
    before_cmdline = (r / "boot/firmware/cmdline.txt").read_text()
    a = cfg(r, "apply", "--release", str(REPO))
    assert a.returncode == 0
    assert (r / "boot/firmware/config.txt").read_text() == before
    assert (r / "boot/firmware/cmdline.txt").read_text() == before_cmdline
    assert "ok       cmdline.txt" in a.stdout
    assert "ok       config.txt" in a.stdout and "ok       etc/systemd" not in a.stdout
    assert "  ok       /etc/systemd/system/inkstant.service" in a.stdout


def test_update_keeps_answers(installed):
    r = installed
    q = cfg(r, "questions", "--update")
    assert q.returncode == 0 and "keeping earlier answers" in q.stdout
    a = cfg(r, "apply", "--release", str(REPO), "--update")
    assert a.returncode == 0
    s = json.loads((r / "var/lib/inkstant/settings.json").read_text())
    assert s["hotspot"]["password"] == "secretpass1"


def test_rerun_with_dev_makes_the_home_network_join_at_start(tmp_path):
    r = fake_root(tmp_path)
    assert cfg(r, "questions", "--yes", "--home-ssid", "Home", "--home-password", "homepass1").returncode == 0
    a = cfg(r, "apply", "--release", str(REPO))
    assert a.returncode == 0 and "inkstant-home-1 (Home)" in a.stdout, a.stdout
    assert cfg(r, "questions", "--yes", "--dev").returncode == 0
    a = cfg(r, "apply", "--release", str(REPO))
    assert a.returncode == 0, a.stdout
    assert "inkstant-home-1 joins at every start: yes" in a.stdout


def test_uninstall_restores_config_and_keeps_photos(installed):
    r = installed
    (r / "var/lib/inkstant/photos/000001.jpg").write_bytes(b"jpeg")
    u = cfg(r, "uninstall", "--yes")
    assert u.returncode == 0, u.stdout + u.stderr
    assert (r / "boot/firmware/config.txt").read_text() == STOCK_CONFIG
    assert (r / "boot/firmware/cmdline.txt").read_text() == STOCK_CMDLINE
    assert not (r / "opt/inkstant").exists() and not (r / "etc/systemd/system/inkstant.service").exists()
    assert (r / "var/lib/inkstant/photos/000001.jpg").exists()
    assert (r / "etc/netplan/50-cloud-init.yaml").exists()
    assert not (r / "etc/cloud/cloud-init.disabled").exists()


def test_config_txt_edit_unit():
    sys.path.insert(0, str(REPO / "install"))
    import configure

    new, commented = configure.edit_config_txt(STOCK_CONFIG, "pi5", "cam0")
    assert "dtoverlay=imx708,cam0" in new and set(commented) == set(configure.CONFLICTING)
    again, commented2 = configure.edit_config_txt(new, "pi5", "cam0")
    assert again == new and commented2 == []
    assert configure.unedit_config_txt(new) == STOCK_CONFIG
    with pytest.raises(configure.Stop):
        configure.edit_config_txt(STOCK_CONFIG + "\ndtoverlay=vc4-kms-v3d,cma-256\n", "pi3", "cam1")


def test_pi3b_has_no_5ghz(tmp_path):
    r = fake_root(tmp_path, model="Raspberry Pi 3 Model B Rev 1.2")
    assert cfg(r, "questions", "--yes", "--no-home").returncode == 0
    assert cfg(r, "apply", "--release", str(REPO)).returncode == 0
    assert "wifi_5ghz = false" in (r / "etc/inkstant/device.toml").read_text()
    s = json.loads((r / "var/lib/inkstant/settings.json").read_text())
    assert s["hotspot"]["ssid"] == "Inkstant-4F2A" and len(s["hotspot"]["password"]) == 10


@pytest.mark.parametrize("model,family", [
    ("Raspberry Pi 3 Model A Plus Rev 1.1", "pi3"),
    ("Raspberry Pi 3 Model B Rev 1.2", "pi3"),
    ("Raspberry Pi 3 Model B Plus Rev 1.3", "pi3"),
    ("Raspberry Pi 4 Model B Rev 1.5", "pi4"),
    ("Raspberry Pi 5 Model B Rev 1.0", "pi5"),
])
def test_supported_models(tmp_path, model, family):
    r = fake_root(tmp_path, model=model)
    assert cfg(r, "preflight").returncode == 0
    assert cfg(r, "questions", "--yes", "--no-home").returncode == 0
    assert cfg(r, "apply", "--release", str(REPO)).returncode == 0
    dev = (r / "etc/inkstant/device.toml").read_text()
    assert f'family = "{family}"' in dev
    assert ("wifi_5ghz = false" in dev) == ("Model B Rev" in model and "Pi 3" in model)


@pytest.mark.parametrize("model", ["Raspberry Pi Zero 2 W Rev 1.0", "Raspberry Pi 2 Model B Rev 1.1",
                                   "Raspberry Pi Compute Module 4 Rev 1.0", "Raspberry Pi Compute Module 5 Rev 1.0",
                                   "Raspberry Pi 400 Rev 1.0", "Raspberry Pi 500 Rev 1.0", "Raspberry Pi 500+ Rev 1.0"])
def test_unsupported_models(tmp_path, model):
    r = fake_root(tmp_path, model=model)
    p = cfg(r, "preflight")
    assert p.returncode == 1, p.stdout


def test_preflight_rejects_wrong_os(tmp_path):
    r = fake_root(tmp_path)
    (r / "etc/os-release").write_text('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nVERSION_CODENAME=bookworm\n')
    p = cfg(r, "preflight")
    assert p.returncode == 1 and "Trixie" in p.stdout


def test_cmdline_edit_unit():
    sys.path.insert(0, str(REPO / "install"))
    import configure

    new, added = configure.edit_cmdline(STOCK_CMDLINE)
    assert added == ["quiet"] and new.endswith(" quiet\n")
    assert configure.edit_cmdline(new) == (new, [])
    assert configure.unedit_cmdline(new, added) == STOCK_CMDLINE
