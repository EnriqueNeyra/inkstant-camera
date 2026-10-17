"""setup.sh, the one-command installer, against a local stand-in for GitHub."""

from __future__ import annotations

import functools
import hashlib
import io
import os
import subprocess
import tarfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"
REPO = "EnriqueNeyra/inkstant-camera"

# Stands in for install/install.sh: records how it was run.
FAKE_INSTALL = """#!/bin/bash
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
{
  echo "args:$*"
  echo "version:$(cat "$here/VERSION")"
  echo "from:$here"
  [ -t 0 ] && echo "stdin:tty" || echo "stdin:other"
} > "$INKSTANT_TEST_OUT"
exit "${INKSTANT_TEST_EXIT:-0}"
"""


def tarball(top: str, version: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data, mode in ((f"{top}/VERSION", f"{version}\n", 0o644),
                                 (f"{top}/install/install.sh", FAKE_INSTALL, 0o755)):
            raw = data.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(raw), mode
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def github(tmp_path):
    site = tmp_path / "site"
    repo = site / REPO
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class GitHub:
        url = f"http://127.0.0.1:{server.server_address[1]}"

        def release(self, version="1.2.3", bad_sum=False):
            d = repo / "releases/latest/download"
            d.mkdir(parents=True, exist_ok=True)
            data = tarball("inkstant", version)
            (d / "inkstant.tar.gz").write_bytes(data)
            digest = "0" * 64 if bad_sum else hashlib.sha256(data).hexdigest()
            (d / "SHA256SUMS").write_text(f"{digest}  inkstant.tar.gz\n")

        def branch(self, path: str, version: str):
            f = repo / "archive" / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(tarball("inkstant-camera-x", version))

    yield GitHub()
    server.shutdown()


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_id = bin_dir / "id"
    fake_id.write_text('#!/bin/sh\n[ "$1" = "-u" ] && { echo 0; exit 0; }\nexec /usr/bin/id "$@"\n')
    fake_id.chmod(0o755)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp),
        "INKSTANT_TEST_OUT": str(tmp_path / "ran.txt"),
    }


def run(env, *args, github_url=None, piped=False, script=SETUP):
    e = dict(env)
    if github_url:
        e["INKSTANT_GITHUB"] = github_url
    if piped:  # curl … | sudo bash -s -- ARGS
        return subprocess.run(["bash", "-s", "--", *args], input=script.read_bytes(), env=e,
                              capture_output=True, timeout=60)
    return subprocess.run(["bash", str(script), *args], env=e, capture_output=True, timeout=60,
                          stdin=subprocess.DEVNULL)


def ran(env) -> dict[str, str]:
    p = Path(env["INKSTANT_TEST_OUT"])
    if not p.exists():
        return {}
    return dict(line.split(":", 1) for line in p.read_text().splitlines())


def test_piped_installs_the_latest_release_with_the_options(github, env):
    github.release("1.2.3")
    r = run(env, "--dev", "--yes", github_url=github.url, piped=True)
    assert r.returncode == 0, r.stderr.decode()
    out = ran(env)
    assert out["args"] == "--dev --yes"
    assert out["version"] == "1.2.3"
    assert out["stdin"] == "other"  # the installer asks on /dev/tty, never on the pipe
    assert b"checksum ok" in r.stdout
    assert list(Path(env["TMPDIR"]).iterdir()) == []  # the download is cleaned up


def test_a_bad_checksum_stops_before_installing(github, env):
    github.release(bad_sum=True)
    r = run(env, github_url=github.url, piped=True)
    assert r.returncode != 0
    assert b"checksum doesn't match" in r.stderr
    assert ran(env) == {}


def test_without_a_release_it_installs_main(github, env):
    github.branch("refs/heads/main.tar.gz", "9.9.9")
    r = run(env, "--dev", github_url=github.url, piped=True)
    assert r.returncode == 0, r.stderr.decode()
    assert b"No release is published yet" in r.stdout
    assert ran(env)["version"] == "9.9.9"
    assert ran(env)["args"] == "--dev"


def test_ref_installs_that_branch_and_is_not_passed_on(github, env):
    github.release("1.2.3")
    github.branch("bench-fixes.tar.gz", "1.3.0")
    r = run(env, "--ref", "bench-fixes", "--no-reboot", github_url=github.url, piped=True)
    assert r.returncode == 0, r.stderr.decode()
    out = ran(env)
    assert out["version"] == "1.3.0"
    assert out["args"] == "--no-reboot"


def test_an_unknown_ref_fails_clearly(github, env):
    r = run(env, "--ref", "nope", github_url=github.url, piped=True)
    assert r.returncode != 0
    assert b"couldn't download 'nope' (HTTP 404)" in r.stderr


def test_no_release_and_no_main_suggests_a_private_repo(github, env):
    r = run(env, github_url=github.url, piped=True)
    assert r.returncode != 0
    assert b"Is the repository public?" in r.stderr


def test_no_network(env):
    r = run(env, github_url="http://127.0.0.1:9", piped=True)
    assert r.returncode != 0
    assert b"couldn't reach GitHub" in r.stderr


def test_the_installers_exit_status_comes_back(github, env):
    github.release()
    r = run({**env, "INKSTANT_TEST_EXIT": "3"}, github_url=github.url, piped=True)
    assert r.returncode == 3


def test_from_a_checkout_it_installs_the_checkout(tmp_path, env):
    checkout = tmp_path / "checkout"
    (checkout / "install").mkdir(parents=True)
    (checkout / "VERSION").write_text("0.5.0\n")
    (checkout / "install/install.sh").write_text(FAKE_INSTALL)
    script = checkout / "setup.sh"
    script.write_bytes(SETUP.read_bytes())
    r = run(env, "--dev", script=script, github_url="http://127.0.0.1:9")  # no network needed
    assert r.returncode == 0, r.stderr.decode()
    out = ran(env)
    assert out["version"] == "0.5.0"
    assert out["args"] == "--dev"
    assert Path(out["from"]).resolve() == checkout.resolve()


def test_help(env):
    r = run(env, "--help")
    assert r.returncode == 0
    assert b"--ref" in r.stdout and b"--dev" in r.stdout
