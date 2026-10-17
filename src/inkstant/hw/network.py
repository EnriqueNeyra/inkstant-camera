"""Wi-Fi through NetworkManager's nmcli.

Profiles are root-only keyfiles: `inkstant-hotspot` and `inkstant-home-1` … `-5`.
They're generated with `nmcli --offline connection add`, which handles SSID and
password escaping, then written atomically and loaded.
"""

from __future__ import annotations

import configparser
import contextlib
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from inkstant.atomic import atomic_write

CONN_DIR = Path("/etc/NetworkManager/system-connections")
HOTSPOT = "inkstant-hotspot"
HOME_PREFIX = "inkstant-home-"
MAX_HOME = 5
HOTSPOT_ADDR = "10.42.0.1"


class NetworkError(Exception):
    pass


def split_terse(line: str) -> list[str]:
    """Split nmcli -t output on unescaped colons, unescaping `\\:` and `\\\\`."""
    fields, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def hotspot_args(ssid: str, password: str) -> list[str]:
    return [
        "type", "wifi", "ifname", "wlan0", "con-name", HOTSPOT, "connection.autoconnect", "no",
        "ssid", ssid, "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
        "ipv4.method", "shared", "ipv4.addresses", f"{HOTSPOT_ADDR}/24", "ipv6.method", "disabled",
        "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp",
        "wifi-sec.group", "ccmp", "wifi-sec.pmf", "disable", "wifi-sec.psk", password,
    ]


def home_args(n: int, ssid: str, password: str | None, autoconnect: bool, key_mgmt: str = "wpa-psk") -> list[str]:
    args = [
        "type", "wifi", "ifname", "wlan0", "con-name", f"{HOME_PREFIX}{n}",
        "connection.autoconnect", "yes" if autoconnect else "no", "ssid", ssid,
    ]
    if password:
        args += ["wifi-sec.key-mgmt", key_mgmt, "wifi-sec.psk", password]
    args += ["ipv4.method", "auto", "ipv6.method", "auto"]
    return args


@dataclass
class HomeNetwork:
    n: int
    ssid: str


class Network:
    def __init__(self, conn_dir: Path = CONN_DIR, run: Callable = subprocess.run, log=None,
                 sleep=time.sleep):
        self.conn_dir = Path(conn_dir)
        self._run = run
        self.log = log or (lambda *_: None)
        self.sleep = sleep

    def nmcli(self, *args: str, timeout: float = 10.0, check: bool = True) -> str:
        try:
            p = self._run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise NetworkError(f"nmcli {args[0]} timed out") from e
        except FileNotFoundError as e:
            raise NetworkError("nmcli isn't installed") from e
        if check and p.returncode != 0:
            raise NetworkError(f"nmcli {' '.join(a for a in args if 'psk' not in a)[:80]}: {p.stderr.strip() or p.returncode}")
        return p.stdout

    # Radio and device ---------------------------------------------------------------
    def radio(self, on: bool) -> None:
        self.nmcli("radio", "wifi", "on" if on else "off", timeout=5)

    def wait_wlan0(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            out = self.nmcli("-t", "-f", "DEVICE,STATE", "device", timeout=5)
            for line in out.splitlines():
                f = split_terse(line)
                if f[0] == "wlan0" and f[1] in ("disconnected", "connected", "connecting"):
                    return
            if time.monotonic() >= deadline:
                raise NetworkError("wlan0 didn't come up")
            self.sleep(0.5)

    def scan(self) -> dict[str, int]:
        """Visible networks: SSID → best signal."""
        out = self.nmcli("-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "ifname", "wlan0",
                         "--rescan", "yes", timeout=12)
        seen: dict[str, int] = {}
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) < 2 or not f[0]:
                continue
            try:
                sig = int(f[1])
            except ValueError:
                sig = 0
            seen[f[0]] = max(sig, seen.get(f[0], 0))
        return seen

    def up(self, profile: str, wait: int) -> bool:
        try:
            self.nmcli("--wait", str(wait), "connection", "up", "id", profile, "ifname", "wlan0", timeout=wait + 5)
            return True
        except NetworkError as e:
            self.log(f"network: {profile}: {e}")
            return False

    def up_hotspot(self) -> bool:
        try:
            self.nmcli("--wait", "15", "connection", "up", "id", HOTSPOT, timeout=20)
            return True
        except NetworkError as e:
            self.log(f"network: hotspot: {e}")
            return False

    def address(self) -> str | None:
        out = self.nmcli("-g", "IP4.ADDRESS", "device", "show", "wlan0", timeout=5)
        for part in out.replace("|", "\n").split():
            ip = part.strip().split("/")[0]
            if ip.count(".") == 3:
                return ip
        return None

    def active_profile(self) -> str | None:
        out = self.nmcli("-t", "-f", "NAME,DEVICE", "connection", "show", "--active", timeout=5)
        for line in out.splitlines():
            f = split_terse(line)
            if len(f) >= 2 and f[1] == "wlan0":
                return f[0]
        return None

    def down(self) -> None:
        name = self.active_profile()
        if name:
            self.nmcli("connection", "down", "id", name, timeout=10, check=False)

    # Profiles -----------------------------------------------------------------------
    def keyfile(self, args: list[str]) -> str:
        return self.nmcli("--offline", "connection", "add", *args, timeout=5)

    def write_profile(self, name: str, text: str) -> None:
        path = self.conn_dir / f"{name}.nmconnection"
        atomic_write(path, text.encode(), mode=0o600)
        self.nmcli("connection", "load", str(path), timeout=5, check=False)

    def remove_profile(self, name: str) -> None:
        self.nmcli("connection", "delete", "id", name, timeout=10, check=False)
        with contextlib.suppress(FileNotFoundError):
            (self.conn_dir / f"{name}.nmconnection").unlink()

    def set_hotspot(self, ssid: str, password: str) -> None:
        self.write_profile(HOTSPOT, self.keyfile(hotspot_args(ssid, password)))

    def set_home(self, n: int, ssid: str, password: str | None, autoconnect: bool = False) -> None:
        if not 1 <= n <= MAX_HOME:
            raise ValueError("home network number out of range")
        self.write_profile(f"{HOME_PREFIX}{n}", self.keyfile(home_args(n, ssid, password, autoconnect)))

    def set_home_autoconnect(self, n: int, on: bool) -> None:
        self.nmcli("connection", "modify", "id", f"{HOME_PREFIX}{n}", "connection.autoconnect",
                   "yes" if on else "no", timeout=5, check=False)

    def homes(self) -> list[HomeNetwork]:
        """Saved home networks, read from our keyfiles (root only)."""
        out = []
        for n in range(1, MAX_HOME + 1):
            path = self.conn_dir / f"{HOME_PREFIX}{n}.nmconnection"
            if not path.exists():
                continue
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read_string(path.read_text())
                ssid = cp.get("wifi", "ssid", fallback=None)
            except (OSError, configparser.Error):
                ssid = None
            if ssid:
                out.append(HomeNetwork(n, keyfile_unescape(ssid)))
        return out

    def free_home_slot(self) -> int | None:
        used = {h.n for h in self.homes()}
        for n in range(1, MAX_HOME + 1):
            if n not in used:
                return n
        return None


def keyfile_unescape(value: str) -> str:
    """Keyfile SSIDs are plain text, or a byte list like `72;111;109;101;` for unusual names."""
    if value.endswith(";") and all(p.isdigit() for p in value.rstrip(";").split(";") if p):
        try:
            return bytes(int(p) for p in value.rstrip(";").split(";")).decode("utf-8", "replace")
        except ValueError:
            return value
    return value


class FakeNetwork:
    """The simulator's network: sharing "at home" on localhost."""

    def __init__(self, url: str = "http://127.0.0.1:8080/", home_ssid: str = "Sim Home", home_visible: bool = True):
        self.url = url
        self.home_visible = home_visible
        self.home = [HomeNetwork(1, home_ssid)]
        self.hotspot = ("Inkstant-SIM", "simulator1")
        self.state = "off"
        self.log: list[str] = []

    def radio(self, on: bool) -> None:
        self.log.append(f"radio {on}")

    def wait_wlan0(self, timeout: float = 10.0) -> None:
        pass

    def scan(self) -> dict[str, int]:
        return {h.ssid: 70 for h in self.home} if self.home_visible else {}

    def up(self, profile: str, wait: int) -> bool:
        self.log.append(f"up {profile}")
        self.state = profile
        return True

    def up_hotspot(self) -> bool:
        self.log.append(f"up {HOTSPOT}")
        self.state = HOTSPOT
        return True

    def address(self) -> str | None:
        return "127.0.0.1"

    def active_profile(self) -> str | None:
        return self.state if self.state != "off" else None

    def down(self) -> None:
        self.log.append("down")
        self.state = "off"

    def set_hotspot(self, ssid: str, password: str) -> None:
        self.hotspot = (ssid, password)

    def set_home(self, n: int, ssid: str, password: str | None, autoconnect: bool = False) -> None:
        self.home = [h for h in self.home if h.n != n] + [HomeNetwork(n, ssid)]
        self.home.sort(key=lambda h: h.n)

    def set_home_autoconnect(self, n: int, on: bool) -> None:
        pass

    def remove_profile(self, name: str) -> None:
        if name.startswith(HOME_PREFIX):
            n = int(name[len(HOME_PREFIX):])
            self.home = [h for h in self.home if h.n != n]

    def homes(self) -> list[HomeNetwork]:
        return list(self.home)

    def free_home_slot(self) -> int | None:
        used = {h.n for h in self.home}
        return next((n for n in range(1, MAX_HOME + 1) if n not in used), None)
