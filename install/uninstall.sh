#!/bin/bash
# Inkstant uninstaller (SPEC.md §15.9). Keeps the photos unless --purge is given and confirmed.
#   sudo /opt/inkstant/current/install/uninstall.sh [--purge] [--yes]
set -Eeuo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Run from a copy: this script's own folder is about to be deleted.
if [ -z "${INKSTANT_UNINSTALL_COPY:-}" ]; then
  tmp="$(mktemp -d)"
  cp -r "$(dirname "$SELF_DIR")"/{install,src} "$tmp"/
  INKSTANT_UNINSTALL_COPY=1 exec bash "$tmp/install/uninstall.sh" "$@"
fi
PURGE=0 YES=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --yes) YES=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "Please run it with sudo." >&2; exit 1; }

confirm() {  # confirm "question" default(y|n)
  [ "$YES" = 1 ] && return 0
  local a
  read -r -p "$1 " a </dev/tty || a="$2"
  case "${a:-$2}" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

if [ "$PURGE" = 1 ] && ! confirm "Delete every photo on this camera too? This can't be undone. [y/N]" n; then
  PURGE=0
fi
args=()
[ "$PURGE" = 1 ] && args+=(--purge)
[ "$YES" = 1 ] && args+=(--yes)
python3 "$SELF_DIR/configure.py" uninstall "${args[@]}"
if dpkg -s pisugar-server >/dev/null 2>&1 && confirm "Remove pisugar-server too (other tools may use it)? [y/N]" n; then
  rm -f /etc/systemd/system/pisugar-server.service.d/inkstant.conf
  DEBIAN_FRONTEND=noninteractive dpkg --purge pisugar-server || true
fi
systemctl daemon-reload
echo "Inkstant is uninstalled."
if confirm "Reboot now? [Y/n]" y; then
  systemctl reboot
fi
