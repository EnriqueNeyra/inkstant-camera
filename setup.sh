#!/bin/bash
# Inkstant setup, in one command (SPEC.md §15.3). On a freshly flashed Raspberry Pi OS Lite
# (64-bit, Trixie), over SSH:
#
#   curl -fsSL https://raw.githubusercontent.com/EnriqueNeyra/inkstant-camera/main/setup.sh | sudo bash
#
# Options go after `bash -s --`, for example developer mode:
#
#   curl -fsSL https://raw.githubusercontent.com/EnriqueNeyra/inkstant-camera/main/setup.sh | sudo bash -s -- --dev
#
# It downloads the latest release (or, before the first release, the latest code on main),
# checks it, and runs install/install.sh, which asks a few questions and reboots at the end.
# From a checkout of the repository, `sudo ./setup.sh` installs that checkout instead.
# Safe to run again.
#
# Everything is inside main(), so bash has read the whole script before any of it runs.
set -Eeuo pipefail

REPO="${INKSTANT_REPO:-EnriqueNeyra/inkstant-camera}"
GITHUB="${INKSTANT_GITHUB:-https://github.com}"

usage() {
  cat <<'EOF'
Usage: setup.sh [--ref BRANCH|TAG] [installer options]

  --ref NAME    install the code on this branch or tag instead of the latest release
  -h, --help    this help

Every other option goes to the installer, for example:
  --dev         developer mode: keeps home Wi-Fi and SSH on after setup
  --yes         accept every default, and reboot at the end without asking
  --no-reboot   finish without rebooting
  --check       run the checks and change nothing
(the full list: install/install.sh --help)
EOF
}

say() { printf '%s\n' "$*"; }
die() { say "setup: $*" >&2; exit 1; }

fetch() {  # fetch URL FILE: prints the HTTP status (000 when the network failed)
  curl -sSL --retry 3 --retry-delay 5 --connect-timeout 20 --max-time 300 \
    -o "$2" -w '%{http_code}' "$1" 2>/dev/null || true
}

main() {
  local ref="" args=() orig=("$@")
  while [ $# -gt 0 ]; do
    case "$1" in
      --ref)
        [ $# -ge 2 ] && [ -n "$2" ] || die "--ref needs a branch or tag name"
        ref="$2"; shift ;;
      --ref=*) ref="${1#--ref=}" ;;
      -h|--help) usage; exit 0 ;;
      *) args+=("$1") ;;
    esac
    shift
  done

  # Run from a checkout (a file next to install/install.sh) rather than piped from curl?
  local self="${BASH_SOURCE[0]:-}" here=""
  if [ -n "$self" ] && [ -f "$self" ]; then
    here="$(cd "$(dirname "$self")" && pwd)"
    [ -f "$here/install/install.sh" ] || here=""
  fi

  if [ "$(id -u)" -ne 0 ]; then
    if [ -n "$self" ] && [ -f "$self" ]; then
      exec sudo bash "$self" ${orig[@]+"${orig[@]}"}
    fi
    die "run it with sudo:  curl -fsSL https://raw.githubusercontent.com/$REPO/main/setup.sh | sudo bash"
  fi

  if [ -n "$here" ] && [ -z "$ref" ]; then
    say "Installing Inkstant from this checkout ($here)."
    exec bash "$here/install/install.sh" ${args[@]+"${args[@]}"}
  fi

  command -v curl >/dev/null || die "curl is missing: sudo apt-get install -y curl"
  local tmp
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/inkstant-setup.XXXXXX")"
  # shellcheck disable=SC2064  # expand $tmp now
  trap "rm -rf '$tmp'" EXIT
  local base="$GITHUB/$REPO" code archive

  if [ -n "$ref" ]; then
    say "Downloading Inkstant ($ref) from $base …"
    archive="$tmp/source.tar.gz"
    code="$(fetch "$base/archive/$ref.tar.gz" "$archive")"
    [ "$code" = 200 ] || die "couldn't download '$ref' (HTTP $code). Check the branch or tag name."
  else
    say "Downloading the latest Inkstant release from $base …"
    code="$(fetch "$base/releases/latest/download/SHA256SUMS" "$tmp/SHA256SUMS")"
    case "$code" in
      200)
        archive="$tmp/inkstant.tar.gz"
        code="$(fetch "$base/releases/latest/download/inkstant.tar.gz" "$archive")"
        [ "$code" = 200 ] || die "couldn't download the release (HTTP $code). Check the internet connection, then run it again."
        grep -q ' inkstant\.tar\.gz$' "$tmp/SHA256SUMS" || die "the release's SHA256SUMS doesn't list inkstant.tar.gz"
        (cd "$tmp" && grep ' inkstant\.tar\.gz$' SHA256SUMS | sha256sum -c --quiet - >/dev/null 2>&1) \
          || die "the download's checksum doesn't match: it was damaged or changed. Run it again."
        say "  checksum ok"
        ;;
      404)
        say "  No release is published yet, so installing the latest code on main."
        archive="$tmp/source.tar.gz"
        code="$(fetch "$base/archive/refs/heads/main.tar.gz" "$archive")"
        [ "$code" = 200 ] || die "couldn't download the code (HTTP $code). Is the repository public? Check the internet connection, then run it again."
        ;;
      000) die "couldn't reach GitHub. Check the Wi-Fi and the time (date), then run it again." ;;
      *) die "GitHub answered HTTP $code. Try again in a few minutes." ;;
    esac
  fi

  mkdir "$tmp/src"
  # Release tarballs have one top folder (inkstant/), and so do GitHub's source archives.
  tar -xzf "$archive" -C "$tmp/src" --strip-components=1 || die "the download isn't a valid archive"
  [ -f "$tmp/src/install/install.sh" ] || die "the download has no install/install.sh"
  say "  Inkstant $(cat "$tmp/src/VERSION" 2>/dev/null || echo '?') downloaded."
  say

  # Not exec: the trap removes the download once the installer finishes. The installer copies
  # what it needs into /opt/inkstant, and asks its questions on the terminal, not stdin.
  local status=0
  bash "$tmp/src/install/install.sh" ${args[@]+"${args[@]}"} </dev/null || status=$?
  exit "$status"
}

main "$@"
