import asyncio
import json
import threading
import time
from datetime import UTC
from pathlib import Path

import pytest

from inkstant.hw import camera, epd, gpio, led, network, pisugar, pisugar_i2c
from inkstant.imaging import epdfile

FIX = Path(__file__).parent / "fixtures"


# Status light --------------------------------------------------------------------------
def test_led_steady_wink_and_cues():
    async def main():
        w = led.FakeLed()
        c = led.LedController(w)
        c.set_steady("ready")
        assert w.log[-1] == ("level", 1)
        c.wink()
        assert w.log[-1] == ("level", 0) and c.shown == "wink"
        c.set_steady("wait")  # during the wink: written when it ends
        await asyncio.sleep(led.WINK_SECONDS + 0.05)
        assert w.log[-1] == ("pattern", led.STEADY["wait"][1], -1)
        c.cue("no_module")  # dropped: cues only over ready, asleep, share
        assert c.queue == type(c.queue)() and c.playing is None
        c.set_steady("ready")
        c.cue("no_module")
        c.cue("battery")
        assert c.playing == "no_module" and list(c.queue) == ["battery"]
        assert w.log[-1] == ("pattern", led.CUES["no_module"][0], 8)
        c.set_steady("problem")  # a non-cue state ends cues at once
        assert c.playing is None and not c.queue
        assert w.log[-1][0] == "pattern" and w.log[-1][1] == led.STEADY["problem"][1]

    asyncio.run(main())


def test_led_cue_sequence_then_steady():
    async def main():
        w = led.FakeLed()
        c = led.LedController(w)
        c.set_steady("asleep")
        c.cue("battery")
        await asyncio.sleep(0.01)
        assert c.playing == "battery"
        c._timer.cancel()
        c._next()  # pretend the 3 s passed
        assert w.log[-1] == ("pattern", led.STEADY["asleep"][1], -1)

    asyncio.run(main())


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
        ("-t", "-f", "SSID,SIGNAL"): "Home:70\nHome:40\n\\:odd:20\n:55\n",
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
    assert n.scan() == {"Home": 70, ":odd": 20}
    assert n.address() == "192.168.1.23"


def test_homes_from_keyfiles(tmp_path):
    (tmp_path / "inkstant-home-2.nmconnection").write_text("[connection]\nid=inkstant-home-2\n[wifi]\nssid=Neyra Home\n")
    (tmp_path / "inkstant-home-4.nmconnection").write_text("[wifi]\nssid=72;105;\n")
    n = network.Network(conn_dir=tmp_path)
    assert [(h.n, h.ssid) for h in n.homes()] == [(2, "Neyra Home"), (4, "Hi")]
    assert n.free_home_slot() == 1
