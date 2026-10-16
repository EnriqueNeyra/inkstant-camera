#!/bin/sh
# RTC wake cycle (SPEC.md §19.5, §8.11): arm the PiSugar alarm a few minutes out and power off.
# The PiSugar should switch the Pi on at that time, and the camera app should treat it as a
# slideshow wake (show the next photo, then power off again on its own).
#
#   sudo sh tools/bench/wake_cycle.sh [minutes]     # default 3
#   sudo sh tools/bench/wake_cycle.sh --report      # after it has woken: what the app decided
#
# Best run on battery (unplugged), with a module in, a few photos taken and the slideshow on.
set -eu

if [ "${1:-}" = "--report" ]; then
  echo "== This boot's wake decision"
  journalctl -b -u inkstant -o short-monotonic --no-pager | grep -E "timing:|wake|slideshow|alarm|shutdown" | head -40 || true
  echo
  echo "== Uptime now: $(cut -d' ' -f1 /proc/uptime) s"
  exit 0
fi

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo sh $0 $*"; exit 1; }
minutes=${1:-3}

# Stop the app first: on stopping, it arms or clears the alarm itself, and saves state.json.
systemctl stop inkstant.service

python3 - "$minutes" <<'EOF'
import json, os, socket, sys, time
from datetime import datetime

SOCK = "/tmp/pisugar-server.sock"

def ask(line, expect):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(3)
        s.connect(SOCK)
        s.sendall((line + "\n").encode())
        buf = b""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            for ln in buf.decode(errors="replace").splitlines():
                if ln.startswith(expect + ":"):
                    return ln.split(":", 1)[1].strip()
        return buf.decode(errors="replace").strip()

when = datetime.now().astimezone().replace(microsecond=0)
when = datetime.fromtimestamp(when.timestamp() + 60 * int(sys.argv[1])).astimezone()
print("PiSugar clock:", ask("get rtc_time", "rtc_time"))
print("Pi clock:     ", datetime.now().astimezone().isoformat(timespec="seconds"))
print("Arming:       ", when.isoformat(), "->", ask(f"rtc_alarm_set {when.isoformat()} 127", "rtc_alarm_set"))
print("Alarm now:    ", ask("get rtc_alarm_enabled", "rtc_alarm_enabled"), ask("get rtc_alarm_time", "rtc_alarm_time"))

# Record the wake as the app does, so the next start is judged a timed (slideshow) wake.
p = "/var/lib/inkstant/state.json"
try:
    with open(p) as f:
        st = json.load(f)
except (OSError, ValueError):
    st = {}
st["next_wake_utc"] = when.timestamp()
tmp = p + ".bench-tmp"
with open(tmp, "w") as f:
    json.dump(st, f)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, p)
print("state.json next_wake_utc =", st["next_wake_utc"])
EOF

echo "Powering off. The Pi should switch on by itself in about $minutes minutes."
echo "Afterwards run: sudo sh $0 --report"
sync
systemctl poweroff
