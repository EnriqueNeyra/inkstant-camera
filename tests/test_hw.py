import asyncio
import json
import os
import threading
import time
from datetime import UTC
from pathlib import Path

import pytest

from inkstant.hw import camera, epd, gpio, led, network, pisugar, pisugar_i2c
from inkstant.imaging import epdfile

FIX = Path(__file__).parent / "fixtures"


# Status light --------------------------------------------------------------------------
def test_led_steady_and_wink():
    async def main():
        w = led.FakeLed()
        c = led.LedController(w)
        c.set_steady("ready")
        assert w.log[-1] == ("level", 1)
        c.wink()
        assert w.log[-1] == ("level", 0) and c.shown == "wink"
        c.set_steady("wait")  # during the wink: written when it ends
        assert w.log[-1] == ("level", 0)
        await asyncio.sleep(led.WINK_SECONDS + 0.05)
        assert w.log[-1] == ("pattern", led.STEADY["wait"][1], -1) and c.shown == "wait"
        c.set_steady("off")
        assert w.log[-1] == ("level", 0) and not c.winking

    asyncio.run(main())


def test_led_has_four_looks():
    # Solid, a slow blink (also the kernel's blink while starting) and off, plus the wink.
    assert set(led.STEADY) == {"starting", "ready", "wait", "off"}


def test_led_soft_fallback_blinks():
    async def main():
        w = led.FakeLed(has_pattern=False)
        c = led.LedController(w)
        c.set_steady("wait")
        await asyncio.sleep(0.05)
        assert w.level == 1
        await asyncio.sleep(0.5)
        assert w.level == 0
        c.close()

    asyncio.run(main())


def test_pattern_steps():
    assert led.pattern_steps("1 500 1 0 0 500 0 0") == [(1, 0.5), (0, 0.5)]


def test_sysfs_led_writes(tmp_path):
    (tmp_path / "trigger").write_text("none [timer] heartbeat pattern\n")
    s = led.SysfsLed(tmp_path)
    assert s.has_pattern()
    s.set_pattern("1 100 1 0 0 3900 0 0", -1)
    assert (tmp_path / "pattern").read_text() == "1 100 1 0 0 3900 0 0"
    assert (tmp_path / "repeat").read_text() == "-1"
    s.set_level(1)
    assert (tmp_path / "brightness").read_text() == "1" and (tmp_path / "trigger").read_text() == "none"


# E-paper driver ------------------------------------------------------------------------
class RecordingSpi:
    def __init__(self, pins, busy_low_for=0.0):
        self.pins = pins
        self.log = []
        self.opened = False
        self.busy_low_for = busy_low_for

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def _busy_pulse(self):
        pin = self.pins.factory.pin(gpio.BUSY)
        pin.drive_low()

        def release():
            time.sleep(self.busy_low_for)
            pin.drive_high()

        threading.Thread(target=release, daemon=True).start()

    def write(self, data):
        dc = self.pins.dc.value
        self.log.append(("data" if dc else "cmd", list(data)))
        if not dc and data[0] in (0x04, 0x12, 0x02):
            self._busy_pulse()

    def write_bulk(self, data):
        self.log.append(("bulk", len(data), self.pins.dc.value))


@pytest.fixture
def pins():
    f = gpio.mock_factory()
    p = gpio.Pins(f)
    yield p
    p.close()


def test_epd_sequence_matches_waveshare(pins):
    pins.factory.pin(gpio.BUSY).drive_high()  # a module answering
    spi = RecordingSpi(pins, busy_low_for=0.05)
    d = epd.EpdDriver(pins, spi=spi)
    shown = []
    d.show(bytes(epdfile.FRAME_BYTES), on_shown=lambda: shown.append(spi.log[-1]))
    cmds = [entry[1][0] for entry in spi.log if entry[0] == "cmd"]
    assert cmds == [c for c, _ in epd.INIT_SEQUENCE] + [0x10, 0x04, 0x06, 0x12, 0x02, 0x07]
    # Each parameter is its own one-byte transfer, as in Waveshare's send_data.
    first = spi.log.index(("cmd", [0xAA]))
    assert spi.log[first + 1: first + 7] == [("data", [b]) for b in (0x49, 0x55, 0x20, 0x08, 0x09, 0x18)]
    assert ("bulk", epdfile.FRAME_BYTES, 1) in spi.log
    assert shown and shown[0] == ("data", [0x00])  # called right after the refresh command's wait
    assert not pins.pwr.value and not pins.rst.value and not pins.dc.value and not spi.opened


def test_epd_no_module_times_out_fast(pins, monkeypatch):
    monkeypatch.setattr(epd, "TIMEOUT_RESET", 0.2)
    d = epd.EpdDriver(pins, spi=RecordingSpi(pins))
    with pytest.raises(epd.ModuleMissing):
        d.show(bytes(epdfile.FRAME_BYTES))
    assert not pins.pwr.value
    assert d.probe() is False


def test_epd_abort_mid_refresh(pins):
    pins.factory.pin(gpio.BUSY).drive_high()
    spi = RecordingSpi(pins, busy_low_for=30)
    d = epd.EpdDriver(pins, spi=spi)
    threading.Timer(0.3, d.request_abort).start()
    t0 = time.monotonic()
    with pytest.raises(epd.Aborted):
        d.show(bytes(epdfile.FRAME_BYTES))
    assert time.monotonic() - t0 < 2
    assert not pins.pwr.value


def test_epd_timeout_raises_display_timeout(pins, monkeypatch):
    monkeypatch.setattr(epd, "TIMEOUT_STEP", 0.3)
    pins.factory.pin(gpio.BUSY).drive_high()
    d = epd.EpdDriver(pins, spi=RecordingSpi(pins, busy_low_for=30))
    with pytest.raises(epd.DisplayTimeout):
        d.show(bytes(epdfile.FRAME_BYTES))


# Camera tuning ------------------------------------------------------------------------
@pytest.mark.parametrize("fixture", ["imx708_vc4.json", "imx708_pisp.json"])
def test_tuning_override(fixture):
    base = json.loads((FIX / fixture).read_text())
    t = camera.build_tuning(base)
    agc = camera.find_algo(t, "rpi.agc")
    assert agc["channels"] and all(ch["speed"] == 0.62 for ch in agc["channels"])
    awb = camera.find_algo(t, "rpi.awb")
    assert awb["speed"] == 0.26 and awb["frame_period"] == 2
    af = camera.find_algo(t, "rpi.af")
    assert af["skip_frames"] == 1
    assert af["speeds"]["normal"]["step_frames"] == 2 and af["speeds"]["normal"]["max_slew"] == 3.0
    assert af["speeds"]["fast"] == camera.find_algo(base, "rpi.af")["speeds"]["fast"]
    assert camera.find_algo(base, "rpi.awb").get("speed") is None  # the original is untouched


def test_tuning_exposure_cap():
    base = json.loads((FIX / "imx708_vc4.json").read_text())
    t = camera.build_tuning(base, exposure_cap_us=33000)
    for ch in camera.find_algo(t, "rpi.agc")["channels"]:
        for mode in ch["exposure_modes"].values():
            assert max(mode["shutter"]) <= 33000


def test_fake_camera_capture():
    cam = camera.FakeCamera(delay=0)
    cam.open()
    got = {}
    shot = cam.capture(0, lambda md: {}, lambda lores, md: got.update(shape=lores.shape))
    assert shot.jpeg[:2] == b"\xff\xd8" and got["shape"] == (972, 1152)
    cam.sleep()
    assert cam.mode == "asleep"
    cam.wake(for_shot=True)
    assert cam.mode == "full"


class FramesPicam2:
    """Stands in for Picamera2's frame waits: each entry is a frame, or None for a timeout."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.waits = []

    def capture_metadata(self, wait=None):
        self.waits.append(wait)
        if not self.frames or self.frames.pop(0) is None:
            raise TimeoutError()  # what Picamera2's job raises: no message
        return {"AfState": 0}

    def set_controls(self, ctrls):
        pass

    def start(self):
        pass


def real_camera(picam2):
    cam = camera.Camera(type("Dev", (), {"camera_fps": None})(), log=lambda *a: None)
    cam.picam2 = picam2
    return cam


def test_settle_waits_per_frame_and_names_a_timeout():
    # A Pi 4 at full resolution: ~1.6 s to the first frame, then ~0.2 s a frame (2026-10-16).
    p = FramesPicam2([{}] * camera.SETTLE_FRAMES)
    real_camera(p)._settle(camera.SETTLE_FRAMES)
    assert p.waits == [camera.FIRST_FRAME_TIMEOUT] + [camera.FRAME_TIMEOUT] * (camera.SETTLE_FRAMES - 1)
    with pytest.raises(camera.CameraError, match="didn't deliver frames"):
        real_camera(FramesPicam2([{}, None]))._settle(camera.SETTLE_FRAMES)


def test_a_late_frame_ends_the_wake_wait(monkeypatch):
    import sys
    import types

    enums = types.SimpleNamespace(AfStateEnum=types.SimpleNamespace(Focused=2, Failed=3))
    monkeypatch.setitem(sys.modules, "libcamera", types.SimpleNamespace(controls=enums))
    cam = real_camera(FramesPicam2([{}, {}, None]))
    cam.mode = "asleep"
    cam.wake(for_shot=True)  # doesn't raise: the capture that follows has its own timeout
    assert cam.mode == "full"


def test_close_forgets_the_tuning_file(tmp_path, monkeypatch):
    f = tmp_path / "tuning.json"
    f.write_text("{}")
    monkeypatch.setenv(camera.TUNING_ENV, str(f))
    cam = real_camera(None)
    cam._tuning_file = str(f)
    cam.close()
    # A path left behind would hide the camera at the next listing (seen on a Pi 4, 2026-10-16).
    assert camera.TUNING_ENV not in os.environ and not f.exists() and cam._tuning_file is None


def test_selftest_says_when_a_module_is_in_but_untested(paths):
    from inkstant import selftest

    def display(present):
        checks = selftest.run(paths, pisugar_get=lambda n: "x", display_probe=lambda: None,
                              module_present=lambda: present)
        return next(c.detail for c in checks if c.name == "Display module")

    assert display(True) == "module in, not tested while idle"
    assert display(False) == "no module in: not tested"


# PiSugar ------------------------------------------------------------------------------
def test_pisugar_client_against_fake_server(paths):
    async def main():
        server = pisugar.FakePiSugarServer(paths.pisugar_sock)
        await server.start()
        taps = []
        client = pisugar.PiSugarClient(paths.pisugar_sock, on_tap=taps.append)
        assert await client.connect(timeout=2)
        try:
            assert await client.battery() == 80.0
            assert await client.plugged() is False
            # A tap mixed into the reply stream doesn't confuse the reply.
            await server.tap("double")
            assert (await client.get("model")) == "PiSugar 3"
            await asyncio.sleep(0.05)
            assert "double" in taps
            problems = await pisugar.ensure_settings(client, "/usr/local/bin/inkstant-ctl power-button")
            assert problems == []
            assert server.values["soft_poweroff_shell"] == "/usr/local/bin/inkstant-ctl power-button"
            assert server.values["soft_poweroff"] == "true"
            # A multi-line error with no prefix becomes an error, not a hang.
            with pytest.raises(pisugar.PiSugarError):
                await client.get("nonsense")
            server.fail_next = "set_auto_power_on"
            with pytest.raises(pisugar.PiSugarError):
                await client.set("set_auto_power_on", "false")
            from datetime import datetime
            await client.alarm_set(datetime(2026, 10, 16, 9, 0, tzinfo=UTC))
            enabled, when = await client.alarm_read()
            assert enabled and when.hour == 9
            # An I2C error now and then (seen on a real PiSugar 3 after an alarm write) is retried,
            # never taken as the value: "is it plugged in?" mustn't become "no".
            server.values["battery_power_plugged"] = "true"
            server.io_errors = {"battery_power_plugged": 2, "rtc_alarm_set": 1}
            assert await client.plugged() is True
            await client.alarm_set(datetime(2026, 10, 16, 10, 0, tzinfo=UTC))
            server.io_errors = {"battery": pisugar.IO_RETRIES}
            with pytest.raises(pisugar.PiSugarError, match="I/O error"):
                await client.battery()
            # The setup checks' blocking reader retries too.
            from inkstant.selftest import pisugar_sync_get
            server.io_errors = {"model": 2}
            get = pisugar_sync_get(paths.pisugar_sock)
            assert await asyncio.get_running_loop().run_in_executor(None, get, "model") == "PiSugar 3"
        finally:
            await client.close()
            await server.stop()

    asyncio.run(main())


def test_pisugar_unavailable(paths):
    async def main():
        client = pisugar.PiSugarClient(paths.pisugar_sock)
        assert not await client.connect(timeout=0.3)
        with pytest.raises(pisugar.PiSugarError):
            await client.battery()

    asyncio.run(main())


# Watchdog -------------------------------------------------------------------------------
def test_watchdog_enable_feed_disable():
    bus = pisugar_i2c.FakeBus()
    w = pisugar_i2c.Watchdog(bus, sleep=lambda s: None)
    w.enable()
    assert bus.regs[0x06] & 0x80 and bus.regs[0x07] == 30 and bus.regs[0x0B] == 0x00
    w.feed()
    assert bus.feeds == 1 and bus.regs[0x06] & 0x80
    w.disable()
    assert not bus.regs[0x06] & 0x80


def test_watchdog_retries_when_relocked():
    bus = pisugar_i2c.FakeBus(relock_at={2, 7})
    w = pisugar_i2c.Watchdog(bus, sleep=lambda s: None)
    w.enable()  # succeeds after retries despite pisugar-server relocking
    assert bus.regs[0x06] & 0x80


def test_watchdog_gives_up():
    class Locked(pisugar_i2c.FakeBus):
        def write_byte_data(self, addr, reg, value):
            if reg == 0x0B:
                return  # never unlocks
            super().write_byte_data(addr, reg, value)

    with pytest.raises(pisugar_i2c.WatchdogError):
        pisugar_i2c.Watchdog(Locked(), sleep=lambda s: None).enable()


# BTN taps -------------------------------------------------------------------------------
def test_tap_reader_reads_and_clears():
    bus = pisugar_i2c.FakeBus()
    r = pisugar_i2c.TapReader(bus)
    assert r.read() is None
    bus.regs[0x08] = 0b1000_0010  # a double tap, beside a bit that isn't ours
    assert r.read() == "double" and bus.regs[0x08] == 0b1000_0000
    assert r.read() is None


def test_tap_reader_keeps_a_tap_it_couldnt_clear():
    class NoWrites(pisugar_i2c.FakeBus):
        fail = True

        def write_byte_data(self, addr, reg, value):
            if self.fail:
                raise OSError(121, "Remote I/O error")
            super().write_byte_data(addr, reg, value)

    bus = NoWrites()
    bus.tap("single")
    r = pisugar_i2c.TapReader(bus)
    with pytest.raises(OSError):
        r.read()
    bus.fail = False
    assert r.read() == "single" and r.read() is None


def _run_reader(reader, until, on_tap=lambda tap: None):
    stop = threading.Event()
    t = threading.Thread(target=reader.run, args=(on_tap, stop))
    t.start()
    try:
        deadline = time.monotonic() + 2
        while not until() and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        stop.set()
        t.join(2)
    assert not t.is_alive()


def test_tap_reader_thread():
    bus = pisugar_i2c.FakeBus()
    bus.tap("long")
    taps = []
    _run_reader(pisugar_i2c.TapReader(bus, interval=0.001), lambda: taps, taps.append)
    assert taps == ["long"]


def test_tap_reader_slows_down_when_the_pisugar_stops_answering():
    class Gone(pisugar_i2c.FakeBus):
        def read_byte_data(self, addr, reg):
            raise OSError(121, "Remote I/O error")

    logs = []
    _run_reader(pisugar_i2c.TapReader(Gone(), log=logs.append, interval=0.001), lambda: logs)
    assert len(logs) == 1 and "once a second" in logs[0]


# Network --------------------------------------------------------------------------------
def test_split_terse():
    assert network.split_terse(r"My\:Net:72") == ["My:Net", "72"]
    assert network.split_terse(r"a\\b:1") == ["a\\b", "1"]


def test_hotspot_and_home_args():
    a = network.hotspot_args("Cam;Net", "pass word!")
    assert a[a.index("ssid") + 1] == "Cam;Net" and "wifi-sec.pmf" in a and a[a.index("wifi-sec.pmf") + 1] == "disable"
    assert "10.42.0.1/24" in a
    h = network.home_args(2, "Home", None, autoconnect=False)
    assert "wifi-sec.psk" not in h and h[h.index("con-name") + 1] == "inkstant-home-2"


def test_network_scan_and_address_parsing():
    outputs = {
        ("-t", "-f", "SSID,SIGNAL,SECURITY"): "Home:70:WPA2 WPA3\nHome:40:WPA2\n\\:odd:20:\n:55:WPA2\nNew:61:WPA3\n",
        ("-g", "IP4.ADDRESS", "device"): "192.168.1.23/24\n",
    }

    class P:
        def __init__(self, out):
            self.stdout, self.stderr, self.returncode = out, "", 0

    def run(args, **kw):
        for key, out in outputs.items():
            if tuple(args[1:4]) == key:
                return P(out)
        return P("")

    n = network.Network(run=run)
    assert n.scan() == {
        "Home": network.Visible(70, frozenset({"WPA2", "WPA3"})),
        ":odd": network.Visible(20),
        "New": network.Visible(61, frozenset({"WPA3"})),
    }
    assert n.address() == "192.168.1.23"


@pytest.mark.parametrize("security, key_mgmt", [
    ("WPA2", "wpa-psk"),
    ("WPA2 WPA3", "wpa-psk"),  # transition: wpa-psk still uses WPA3 when the chip can
    ("WPA1 WPA2", "wpa-psk"),
    ("WPA3", "sae"),
    ("", None),
    ("OWE", None),
])
def test_the_profile_a_network_in_range_needs(security, key_mgmt):
    assert network.Visible(50, frozenset(security.split())).key_mgmt == key_mgmt


def test_set_home_key_mgmt_keeps_the_password():
    calls, logs = [], []

    class P:
        def __init__(self, rc):
            self.stdout, self.stderr, self.returncode = "", "Error: no such connection" if rc else "", rc

    n = network.Network(run=lambda args, **kw: calls.append(args) or P(0), log=logs.append)
    assert n.set_home_key_mgmt(3, "sae")
    # Only the security changes: nmcli keeps the saved password, so it's never passed (or logged) again.
    assert calls == [["nmcli", "connection", "modify", "id", "inkstant-home-3", "wifi-sec.key-mgmt", "sae"]]
    n = network.Network(run=lambda args, **kw: P(10), log=logs.append)
    assert not n.set_home_key_mgmt(3, "wpa-psk")
    assert logs and "inkstant-home-3" in logs[0] and "no such connection" in logs[0]


def test_homes_from_keyfiles(tmp_path):
    (tmp_path / "inkstant-home-2.nmconnection").write_text(
        "[connection]\nid=inkstant-home-2\n[wifi]\nssid=Neyra Home\n[wifi-security]\nkey-mgmt=sae\npsk=secret123\n")
    (tmp_path / "inkstant-home-4.nmconnection").write_text("[wifi]\nssid=72;105;\n")
    n = network.Network(conn_dir=tmp_path)
    assert [(h.n, h.ssid, h.key_mgmt) for h in n.homes()] == [(2, "Neyra Home", "sae"), (4, "Hi", None)]
    assert n.free_home_slot() == 1
