#!/bin/sh
# Boot-to-ready: systemd's view of the boot plus the camera app's own
# timing marks (app start, wake decision, ready). Run it after a cold boot (power off, then on).
#
#   sh tools/bench/boot_timing.sh
set -u

out=/var/lib/inkstant/bench
mkdir -p "$out" 2>/dev/null || out=.
file="$out/boot_timing-$(date +%Y%m%d-%H%M%S).txt"
{
  echo "== $(tr -d '\0' < /proc/device-tree/model 2>/dev/null), kernel $(uname -r)"
  echo "== cmdline: $(cat /proc/cmdline)"
  echo
  echo "== systemd-analyze"
  systemd-analyze 2>&1
  echo
  echo "== critical chain to inkstant.service"
  systemd-analyze critical-chain inkstant.service 2>&1
  echo
  echo "== slowest units"
  systemd-analyze blame 2>&1 | head -15
  echo
  echo "== camera app timing marks (uptime in seconds)"
  journalctl -b -u inkstant -o cat --no-pager 2>/dev/null | grep "timing:" || echo "(none: is the app running?)"
  echo
  echo "== enabled services"
  systemctl list-unit-files --state=enabled --no-pager --no-legend 2>/dev/null | awk '{print $1}' | tr '\n' ' '
  echo
} | tee "$file"
echo
echo "Saved to $file"
