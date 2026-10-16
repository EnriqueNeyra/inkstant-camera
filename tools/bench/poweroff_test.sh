#!/bin/sh
# Delayed power-off (SPEC.md §19.5, §13.4): `poweroff` runs our shutdown hook, which arms the
# PiSugar's power cut about 10 s later. Time it with a stopwatch: start when this script says
# "powering off", stop when the PiSugar's power LEDs go out (or the USB meter reads 0).
#
#   sudo sh tools/bench/poweroff_test.sh            # power off through the camera app (normal path)
#   sudo sh tools/bench/poweroff_test.sh --direct   # plain `poweroff`, app not involved
#   sudo sh tools/bench/poweroff_test.sh --reboot   # must NOT cut power: the Pi comes back
#
# After the next boot, `sudo sh tools/bench/poweroff_test.sh --report` prints what the last
# shutdown logged.
set -eu

HOOK=/usr/lib/systemd/system-shutdown/inkstant-poweroff
mode=${1:-app}

if [ "$mode" = "--report" ]; then
  echo "== Last boot's shutdown (journal of the previous boot)"
  journalctl -b -1 -o short-monotonic --no-pager | grep -E "inkstant|PiSugar|reboot|Power-Off|poweroff" | tail -40 || true
  echo
  echo "== App's last_shutdown record"
  python3 -c 'import json;print(json.load(open("/var/lib/inkstant/state.json")).get("last_shutdown"))' 2>/dev/null || true
  exit 0
fi

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo sh $0 $*"; exit 1; }
[ -x "$HOOK" ] || { echo "FAIL: $HOOK is missing or not executable"; exit 1; }
command -v i2cget >/dev/null || { echo "FAIL: i2c-tools isn't installed"; exit 1; }
i2cget -y 1 0x57 0x00 >/dev/null 2>&1 || { echo "FAIL: no PiSugar at I2C 0x57"; exit 1; }
echo "PiSugar found; hook installed. Journal is persistent: $(test -d /var/log/journal && echo yes || echo no)"
echo "Writing everything to disk ..."
sync
case "$mode" in
  --direct)
    echo "powering off (plain poweroff) -- start the stopwatch"
    systemctl poweroff ;;
  --reboot)
    echo "rebooting -- the power must stay on and the Pi must come back"
    systemctl reboot ;;
  *)
    if systemctl is-active --quiet inkstant.service; then
      echo "powering off through the camera app -- start the stopwatch"
      inkstant-ctl shutdown
    else
      echo "camera app isn't running; powering off directly -- start the stopwatch"
      systemctl poweroff
    fi ;;
esac
