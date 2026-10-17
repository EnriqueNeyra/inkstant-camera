#!/bin/bash
# Inkstant setup script (SPEC.md §15). Turns a freshly flashed Raspberry Pi OS Lite (64-bit,
# Trixie) into an Inkstant camera. Safe to run again: finished steps print "ok".
#
#   sudo ./inkstant/install/install.sh [--yes] [--dev] [--no-reboot] [--reconfigure] [--check] ...
#
# This half needs root, the network and apt; install/configure.py writes every file.
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE="$(dirname "$SELF_DIR")"
CONFIGURE="$SELF_DIR/configure.py"
LOG=/var/log/inkstant-install.log
LOCK=/run/inkstant-install.lock
PISUGAR_VERSION=2.3.5
PISUGAR_DEB="pisugar-server_${PISUGAR_VERSION}-1_arm64.deb"
PISUGAR_URL="https://github.com/PiSugar/pisugar-power-manager-rs/releases/download/v${PISUGAR_VERSION}/${PISUGAR_DEB}"
CACHE=/var/cache/inkstant
APT_OPTS=(-y -o DPkg::Lock::Timeout=300 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
PACKAGES=(python3-pil python3-numpy python3-gpiozero python3-lgpio python3-spidev python3-smbus2
          python3-flask python3-waitress python3-qrcode python3-piexif fonts-dejavu-core i2c-tools
          util-linux-extra)

YES=0 UPDATE=0 RECONFIGURE=0 CHECK=0 FORCE_MODEL=0 NO_REBOOT=0 DEV=0
QUESTION_FLAGS=()
STEP="start"

usage() {
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'
Flags:
  --yes                 accept every default, and reboot at the end without asking
  --update              used by the updater: no questions, keep answers, don't restart or reboot
  --reconfigure         ask the questions again and apply the answers; skip package installs
  --check               run the checks and report; change nothing
  --from-source         install this checkout instead of a release tarball (same thing here)
  --force-model         continue on an unsupported board
  --no-reboot           finish without rebooting
  --dev                 turn on developer mode (home Wi-Fi and SSH at every start)
  --hotspot-ssid NAME   --hotspot-password PW   --home-ssid NAME   --home-password PW   --no-home
  --country CC          --camera-rotate 0|180   --display-rotation 0|180   --camera-port cam0|cam1
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1 ;;
    --update) UPDATE=1 ;;
    --reconfigure) RECONFIGURE=1 ;;
    --check) CHECK=1 ;;
    --from-source) ;;
    --force-model) FORCE_MODEL=1 ;;
    --no-reboot) NO_REBOOT=1 ;;
    --dev) DEV=1 ;;
    --no-home) QUESTION_FLAGS+=("$1") ;;
    --hotspot-ssid|--hotspot-password|--home-ssid|--home-password|--country|--camera-rotate|--display-rotation|--camera-port)
      [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; }
      QUESTION_FLAGS+=("$1" "$2"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run it with sudo:  sudo $0 $*" >&2
  exit 1
fi

# One copy at a time, and a log of everything (but never the passwords: they aren't printed).
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Another install is already running." >&2
  exit 1
fi
touch "$LOG" && chmod 600 "$LOG"
# tee passes partial lines straight through; questions go to /dev/tty, so passwords never reach the log.
exec > >(tee -a "$LOG") 2>&1

on_error() {
  local code=$?
  echo
  echo "STOPPED at step: $STEP (exit $code)."
  echo "The last lines of the log ($LOG):"
  tail -n 15 "$LOG" | sed 's/^/  /'
  echo "Fix what it says above, then run the same command again. It's safe to run the script again."
  exit "$code"
}
trap on_error ERR

say()  { printf '%s\n' "$*"; }
step() { STEP="$1"; echo; say "== $1"; }
stop() { say "  STOP: $*"; say "  It's safe to run the script again once that's fixed."; exit 1; }
py()   { python3 "$CONFIGURE" "$@"; }

family=""
detect_family() {
  local model
  model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
  case "$model" in
    *"Raspberry Pi 5 Model B"*) family=pi5 ;;
    *"Raspberry Pi 4 Model B"*) family=pi4 ;;
    *"Raspberry Pi 3 Model"*) family=pi3 ;;
    *) family=unknown ;;
  esac
  say "  board: $model"
}

retry() {  # retry N CMD...: run CMD up to N times, waiting between tries
  local n=$1; shift
  local i
  for ((i = 1; i <= n; i++)); do
    if "$@"; then return 0; fi
    [ "$i" -lt "$n" ] && { say "  retrying ($i of $n) …"; sleep 10; }
  done
  return 1
}

# 1 ------------------------------------------------------------------------------------------
step "1 Start"
say "Inkstant $(cat "$RELEASE/VERSION" 2>/dev/null || echo '?') setup, $(date '+%F %T')"
detect_family
dpkg --configure -a >/dev/null 2>&1 || true

# 2 ------------------------------------------------------------------------------------------
step "2 Preflight checks"
pf=()
[ "$FORCE_MODEL" = 1 ] && pf+=(--force-model)
py preflight "${pf[@]}" || stop "Fix the problems above, then run the script again."

if [ "$CHECK" = 1 ]; then
  step "Check only"
  PYTHONPATH="$RELEASE/src" python3 -m inkstant ctl selftest --install || true
  say "Nothing was changed (--check)."
  exit 0
fi

# 3 ------------------------------------------------------------------------------------------
step "3 Questions"
qf=("${QUESTION_FLAGS[@]}")
[ "$YES" = 1 ] && qf+=(--yes)
[ "$UPDATE" = 1 ] && qf+=(--update)
[ "$RECONFIGURE" = 1 ] && qf+=(--reconfigure)
[ "$DEV" = 1 ] && qf+=(--dev)
py questions "${qf[@]}"

# 4 ------------------------------------------------------------------------------------------
step "4 Packages"
if [ "$RECONFIGURE" = 1 ]; then
  say "  ok       skipped (--reconfigure)"
else
  retry 3 apt-get update -q || stop "apt-get update failed three times. Check the internet connection."
  export DEBIAN_FRONTEND=noninteractive
  # --no-install-recommends skips Picamera2's Qt and OpenGL preview packages.
  retry 3 apt-get install "${APT_OPTS[@]}" --no-install-recommends python3-picamera2 \
    || stop "Installing python3-picamera2 failed."
  # rpicam-apps is built against one exact libcamera build, but only asks for "this one or
  # newer", so the line above can leave it behind: rpicam-hello 1.13.0 crashed against
  # libcamera 0.7.2+rpt20261009 (2026-10-16). Upgrade whichever of these is installed.
  retry 3 apt-get install "${APT_OPTS[@]}" --only-upgrade librpicam-app1 rpicam-apps-core rpicam-apps-lite rpicam-apps \
    || say "  warning  couldn't upgrade rpicam-apps; rpicam-hello may not work, the camera app is unaffected"
  retry 3 apt-get install "${APT_OPTS[@]}" "${PACKAGES[@]}" || stop "Installing packages failed."
fi
pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[ "$pyver" = "3.13" ] || say "  warning  Python is $pyver; Inkstant is built for 3.13"
python3 -c "import picamera2" 2>/dev/null || stop "Picamera2 doesn't import. Run: sudo apt-get install --reinstall python3-picamera2"
if [ "$family" != pi5 ] && [ ! -f /usr/share/libcamera/pipeline/rpi/vc4/rpi_apps.yaml ]; then
  stop "libcamera's rpi_apps.yaml is missing, so the camera would need too much memory. Reinstall python3-libcamera."
fi
say "  ok       packages"

# Wi-Fi country, if the questions asked for one
country="$(python3 -c 'import json; print(json.load(open("/var/lib/inkstant/install-state.json"))["answers"].get("country") or "")' 2>/dev/null || true)"
if [ -n "$country" ]; then
  raspi-config nonint do_wifi_country "$country" && say "  changed  Wi-Fi country $country"
fi

# 5 ------------------------------------------------------------------------------------------
step "5 Interfaces (I2C, SPI)"
raspi-config nonint do_i2c 0
raspi-config nonint do_spi 0
modprobe i2c-dev 2>/dev/null || true
modprobe spidev 2>/dev/null || true
[ -e /dev/i2c-1 ] || stop "/dev/i2c-1 is missing after turning I2C on. Reboot, then run the script again."
[ -e /dev/spidev0.0 ] || say "  note     /dev/spidev0.0 appears after the reboot"
say "  ok       I2C and SPI on"

# 6 ------------------------------------------------------------------------------------------
step "6 PiSugar"
if ! i2cget -y 1 0x57 0x00 >/dev/null 2>&1; then
  stop "No PiSugar on I2C (address 0x57). Check the PiSugar is attached and switched on (press its power button)."
fi
installed="$(dpkg-query -W -f='${Version}' pisugar-server 2>/dev/null || true)"
if [ "$installed" != "${PISUGAR_VERSION}-1" ]; then
  mkdir -p "$CACHE"
  retry 3 curl -fsSL --max-time 300 -o "$CACHE/$PISUGAR_DEB" "$PISUGAR_URL" || stop "Couldn't download pisugar-server."
  (cd "$CACHE" && sha256sum -c "$SELF_DIR/pisugar-server.sha256") >/dev/null \
    || { rm -f "$CACHE/$PISUGAR_DEB"; stop "pisugar-server's checksum doesn't match; the download was damaged or changed."; }
  # Web UI and TCP off, socket on (SPEC.md §13.5). Without jq the post-install skips its login prompt.
  debconf-set-selections <<'EOF'
pisugar-server pisugar-server/model select PiSugar 3
pisugar-server pisugar-server/address select 127.0.0.1
pisugar-server pisugar-server/web boolean false
pisugar-server pisugar-server/tcp boolean false
pisugar-server pisugar-server/uds boolean true
EOF
  DEBIAN_FRONTEND=noninteractive dpkg -i "$CACHE/$PISUGAR_DEB" || stop "Installing pisugar-server failed."
  say "  changed  pisugar-server $PISUGAR_VERSION installed"
else
  say "  ok       pisugar-server $PISUGAR_VERSION"
fi
install -D -m 0644 "$RELEASE/system/systemd/pisugar-server.service.d/inkstant.conf" \
  /etc/systemd/system/pisugar-server.service.d/inkstant.conf
systemctl daemon-reload
systemctl enable pisugar-server.service >/dev/null 2>&1
systemctl restart pisugar-server.service
for _ in $(seq 1 20); do [ -S /tmp/pisugar-server.sock ] && break; sleep 0.5; done
[ -S /tmp/pisugar-server.sock ] || stop "pisugar-server didn't start. See: journalctl -u pisugar-server"
pisugar_info="$(PYTHONPATH="$RELEASE/src" python3 - <<'EOF'
from inkstant.selftest import pisugar_sync_get, parse_version
from pathlib import Path
get = pisugar_sync_get(Path("/tmp/pisugar-server.sock"))
model, fw = get("model"), get("firmware_version")
print(model)
print(fw)
print("ok" if parse_version(fw) >= (1, 2, 4) else "old")
EOF
)" || stop "pisugar-server doesn't answer on its socket."
p_model="$(sed -n 1p <<<"$pisugar_info")"; p_fw="$(sed -n 2p <<<"$pisugar_info")"; p_ok="$(sed -n 3p <<<"$pisugar_info")"
[ "$p_model" = "PiSugar 3" ] || stop "pisugar-server reports '$p_model'; it must be set up for a PiSugar 3."
[ "$p_ok" = ok ] || stop "PiSugar firmware $p_fw is too old: 1.2.4 or later is needed for the power-off. Contact PiSugar support for a firmware update."
ss -ltnH 2>/dev/null | grep -Eq ':(8421|8422|8423)\b' && say "  warning  pisugar-server still listens on a TCP port"
say "  ok       PiSugar 3, firmware $p_fw, socket root-only"

# 7 ------------------------------------------------------------------------------------------
step "7 Camera check"
if command -v rpicam-hello >/dev/null 2>&1; then
  cams="$(timeout 30 rpicam-hello --list-cameras 2>&1 || true)"
  if grep -q "imx708" <<<"$cams"; then
    say "  ok       camera found: $(grep -m1 -o 'imx708[^ ]*' <<<"$cams")"
  elif grep -q "No cameras available" <<<"$cams"; then
    say "  warning  No camera found: check the ribbon cable and its direction (HARDWARE.md §9.3)."
  else
    say "  warning  couldn't list cameras yet (it may need the reboot)"
  fi
else
  say "  note     rpicam-hello isn't installed; the setup check tests the camera after the reboot"
fi

# 8 to 12 --------------------------------------------------------------------------------------
step "8-12 App files, settings, Wi-Fi, system files"
af=(--release "$RELEASE")
[ "$UPDATE" = 1 ] && af+=(--update)
py apply "${af[@]}"

step "11 PiSugar settings and clock"
PYTHONPATH="$RELEASE/src" py pisugar || stop "The PiSugar settings didn't stick."

# 13 ---------------------------------------------------------------------------------------------
step "13 Checks before reboot"
PYTHONPATH=/opt/inkstant/current/src python3 -m inkstant ctl selftest --install \
  || stop "A setup check failed (above). Fix it, then run the script again."

# 14 ---------------------------------------------------------------------------------------------
step "14 Finish"
py summary
if [ "$UPDATE" = 1 ] || [ "$NO_REBOOT" = 1 ]; then
  say "Not rebooting now. The changes take effect at the next start."
  exit 0
fi
if [ "$YES" = 1 ]; then
  say "Rebooting in 5 seconds …"
  sleep 5
  systemctl reboot
  exit 0
fi
read -r -p "Reboot now? [Y/n] " answer </dev/tty || answer=y
case "${answer:-y}" in
  [Nn]*) say "Reboot later with: sudo reboot" ;;
  *) say "Rebooting …"; systemctl reboot ;;
esac
