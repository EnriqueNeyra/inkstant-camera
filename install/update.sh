#!/bin/bash
# Inkstant updater. Run by the camera app as the transient unit inkstant-update:
#   systemd-run --unit=inkstant-update --collect -p RuntimeMaxSec=1800 .../install/update.sh VERSION
set -Eeuo pipefail
VERSION="${1:?usage: update.sh VERSION}"
REPO="EnriqueNeyra/inkstant-camera"
BASE="https://github.com/$REPO/releases/download/v$VERSION"
CTL=/usr/local/bin/inkstant-ctl
WORK="$(mktemp -d /var/tmp/inkstant-update.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

report() { "$CTL" update-report "$@" >/dev/null 2>&1 || true; }
record() {  # record STATE MESSAGE
  python3 - "$1" "$VERSION" "$2" <<'PY'
import json, sys, time
state, version, message = sys.argv[1:4]
json.dump({"state": state, "version": version, "message": message, "at": int(time.time())},
          open("/var/lib/inkstant/update.json", "w"))
PY
}
fail() {
  echo "update failed: $1" >&2
  record failed "$1"
  report failed "" "$1"
  exit 1
}
trap 'fail "unexpected error (line $LINENO)"' ERR

report running downloading
curl -fsSL --retry 3 --max-time 600 -o "$WORK/inkstant.tar.gz" "$BASE/inkstant.tar.gz" || fail "download failed"
curl -fsSL --retry 3 --max-time 60 -o "$WORK/SHA256SUMS" "$BASE/SHA256SUMS" || fail "download failed"
(cd "$WORK" && sha256sum -c --ignore-missing SHA256SUMS >/dev/null) || fail "the download's checksum doesn't match"

report running installing
tar -xzf "$WORK/inkstant.tar.gz" -C "$WORK" || fail "couldn't unpack the update"
[ -x "$WORK/inkstant/install/install.sh" ] || fail "the update is missing its installer"
"$WORK/inkstant/install/install.sh" --update --yes --no-reboot || fail "installing failed (see /var/log/inkstant-install.log)"
record "done" "installed version $VERSION"

report running restarting
# The camera app runs its shutdown sequence, then restarts (or powers off if PWR was pressed).
"$CTL" shutdown --reboot || systemctl reboot
