"""The camera app end to end, with every hardware fake."""

import asyncio
import http.server
import json
import threading
import time
import urllib.error

import pytest

from inkstant import ipc, settings, state
from inkstant.camera_app import machine
from inkstant.camera_app.app import CameraApp, Hardware
from inkstant.hw import camera, epd, gpio, led, network, pisugar, pisugar_i2c, system
from inkstant.imaging import epdfile, palette


class FakeClock:
    def __init__(self):
        self.t = 10_000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def health_url():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/healthz"
    srv.shutdown()


class Harness:
    def __init__(self, paths, health_url):
        self.paths = paths
        self.health_url = health_url
        self.logs = []

    async def start(self, module=True, battery="80.0", plugged="false", cfg=None, epd_speed=0.002,
                    camera_fail=False, wait_ready=True):
        p = self.paths
        # The first-start setup check would light the problem pattern on a laptop; skip it here.
        p.selftest.write_text(json.dumps({"version": __import__("inkstant.version").version.VERSION,
                                          "passed": True, "failed": [], "checks": []}))
        if cfg:
            settings.save(p.settings, settings.merge(settings.validate(settings.DEFAULTS), cfg))
        self.factory = gpio.mock_factory()
        self.pins = gpio.Pins(self.factory)
        if module:
            self.factory.pin(gpio.DETECT).drive_low()
        self.server = pisugar.FakePiSugarServer(p.pisugar_sock)
        self.server.values["battery"] = battery
        self.server.values["battery_power_plugged"] = plugged
        await self.server.start()
        self.led = led.FakeLed()
        self.epd = epd.FakeEpd(p.root / "display.png", self.pins.module_present, speed=epd_speed)
        self.cam = camera.FakeCamera(delay=0, fail_open=camera_fail)
        self.net = network.FakeNetwork()
        self.sys = system.FakeSystem()
        self.bus = pisugar_i2c.FakeBus()
        hw = Hardware(pins=self.pins, led_writer=self.led, epd=self.epd, camera_factory=lambda: self.cam,
                      network=self.net, system=self.sys, watchdog=pisugar_i2c.Watchdog(self.bus, sleep=lambda s: None),
                      gallery_health_url=self.health_url, start_gallery=lambda: None, stop_gallery=lambda: None,
                      trust_all_callers=True)
        self.clock = FakeClock()
        self.app = CameraApp(p, hw, log=self.logs.append, clock=self.clock)
        self.task = asyncio.create_task(self.app.run())
        await self.until(lambda: self.app.lifecycle != machine.STARTING)
        if wait_ready and self.app.lifecycle == machine.ACTIVE:
            await self.until(lambda: self.app.camera_state in ("ready", "fallback", "failed"))
            await self.until(lambda: self.app.display_job is None)

    async def until(self, cond, timeout=8.0):
        deadline = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > deadline:
                raise AssertionError("timed out; log:\n" + "\n".join(self.logs[-30:]))
            await asyncio.sleep(0.01)

    async def press(self):
        pin = self.factory.pin(gpio.SHUTTER)
        pin.drive_low()
        await asyncio.sleep(0.05)
        pin.drive_high()
        await asyncio.sleep(0.05)

    async def shoot(self):
        n = len(self.app.store.list())
        await self.press()
        await self.until(lambda: len(self.app.store.list()) == n + 1 and not self.app.capture_busy)
        return self.app.store.list()[0].id

    async def remove_module(self):
        self.factory.pin(gpio.DETECT).drive_high()
        await self.until(lambda: not self.app.module_present)

    async def insert_module(self):
        self.factory.pin(gpio.DETECT).drive_low()
        await self.until(lambda: self.app.module_present)

    async def idle_display(self):
        await self.until(lambda: self.app.display_job is None and self.app.pending_job is None)

    async def cmd(self, name, **args):
        return await self.app.handle_command(name, args, "root")

    async def stop(self):
        if not self.task.done():
            self.app.request_shutdown("system")
            with __import__("contextlib").suppress(Exception):
                await asyncio.wait_for(self.task, 10)
        await self.server.stop()
        self.pins.close()

    def steady(self):
        return self.app.led.steady


def run(coro):
    asyncio.run(coro)


def test_power_on_shoot_develop(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            assert h.app.lifecycle == machine.ACTIVE and h.steady() == "ready"
            assert ("timer",) not in h.led.log  # the kernel's starting blink was left alone
            pid = await h.shoot()
            assert ("level", 0) in h.led.log  # the wink
            await h.idle_display()
            assert h.app.state.current == pid and h.app.state.undeveloped is None
            assert h.app.state.display_content == "photo"
            assert len(h.epd.shown) == 1 and (paths.photos / f"{pid}.jpg").exists()
            assert h.steady() == "ready"
            assert h.app.state.next_seq == 2
            assert ("notify", "READY=1") in h.sys.calls
            assert h.bus.regs[0x06] & 0x80  # watchdog on
            assert h.server.values["soft_poweroff_shell"].endswith("inkstant-ctl power-button")
        finally:
            await h.stop()

    run(main())


def test_no_module_then_insert_develops_newest(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False)
        try:
            ids = [await h.shoot() for _ in range(5)]
            assert h.epd.shown == [] and h.app.state.undeveloped == ids[-1]
            assert any(e[0] == "pattern" and e[1] == led.CUES["no_module"][0] for e in h.led.log)
            await h.insert_module()
            await h.idle_display()
            assert len(h.epd.shown) == 1 and h.app.state.current == ids[-1]
        finally:
            await h.stop()

    run(main())


def test_pull_out_mid_develop_keeps_photo_undeveloped(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(epd_speed=0.05)
        try:
            pid = await h.shoot()
            await h.until(lambda: h.app.display == "refreshing")
            await h.remove_module()
            await h.until(lambda: h.app.display_job is None)
            assert h.app.state.undeveloped == pid and h.epd.shown == []
            await h.insert_module()
            await h.idle_display()
            assert h.app.state.current == pid
        finally:
            await h.stop()

    run(main())


def test_sleep_then_shutter_wakes_and_shoots(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            h.clock.advance(5 * 60 + 1)
            await h.app.tick()
            assert h.app.camera_state == "asleep" and h.steady() == "asleep"
            await h.shoot()
            assert "wake shot" in h.cam.history and h.app.camera_state == "ready"
        finally:
            await h.stop()

    run(main())


def test_auto_off_arms_wake_and_keeps_photo(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            await h.shoot()
            await h.idle_display()
            shown = len(h.epd.shown)
            h.clock.advance(10 * 60 + 1)
            await h.app.tick()
            await asyncio.wait_for(h.task, 10)
            assert ("poweroff",) in h.sys.calls
            assert h.server.values["rtc_alarm_enabled"] == "true"
            assert len(h.epd.shown) == shown  # no white screen: a wake is scheduled
            st = state.load_state(paths.state)
            assert st.next_wake_utc and st.last_shutdown["reason"] == "auto_off"
            assert not h.bus.regs[0x06] & 0x80  # watchdog off before power-off
        finally:
            await h.stop()

    run(main())


def test_white_screen_when_no_wake(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(cfg={"slideshow": {"enabled": False}})
        try:
            await h.shoot()
            await h.idle_display()
            await h.cmd("power.button")
            await asyncio.wait_for(h.task, 10)
            assert h.epd.shown[-1] == epdfile.solid(epdfile.WHITE_CODE)
            assert h.server.values["rtc_alarm_enabled"] == "false"
            assert state.load_state(paths.state).display_content == "blank"
        finally:
            await h.stop()

    run(main())


@pytest.mark.parametrize("stopping, white", [(None, False), ("reboot", False), ("poweroff", True)])
def test_outside_stop_whitens_only_at_power_off(paths, health_url, stopping, white):
    # `systemctl restart inkstant` (an install, a crash) used to wipe the photo (seen on a Pi 4).
    async def main():
        h = Harness(paths, health_url)
        await h.start(cfg={"slideshow": {"enabled": False}})
        try:
            await h.shoot()
            await h.idle_display()
            shown = len(h.epd.shown)
            h.sys.stopping = stopping
            h.app._on_sigterm()
            await asyncio.wait_for(h.task, 10)
            assert (len(h.epd.shown) > shown) == white
            assert (state.load_state(paths.state).display_content == "blank") == white
        finally:
            await h.stop()

    run(main())


def test_battery_saver_automatic_and_plug_in(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(battery="19.0")
        try:
            assert h.app.state.saver_auto_active and h.app.saver
            assert machine.effective_minutes(h.app.settings, True) == (1, 5, 5)
            assert h.cam.fps == camera.SAVER_FPS
            # Turning it off by hand dismisses it until the next plug-in.
            await h.cmd("settings.set", battery_saver={"enabled": False})
            assert not h.app.saver and h.app.state.saver_auto_dismissed
            await h.app.read_battery()
            assert not h.app.saver
            h.server.values["battery_power_plugged"] = "true"
            await h.app.read_battery()
            assert not h.app.state.saver_auto_dismissed
        finally:
            await h.stop()

    run(main())


def test_low_battery_shutdown_whitens_and_disables_alarm(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(battery="4.0")
        try:
            await h.app.read_battery()
            await asyncio.wait_for(h.task, 10)
            assert state.load_state(paths.state).last_shutdown["reason"] == "low_battery"
            assert h.server.values["rtc_alarm_enabled"] == "false"
            assert h.epd.shown and h.epd.shown[-1] == epdfile.solid(epdfile.WHITE_CODE)
        finally:
            await h.stop()

    run(main())


def test_timed_wake_shows_next_photo_and_rearms(paths, health_url):
    async def main():
        # A first session makes photos.
        h = Harness(paths, health_url)
        await h.start()
        ids = []
        for _ in range(3):
            ids.append(await h.shoot())
            await h.idle_display()
        await h.cmd("power.button")
        await asyncio.wait_for(h.task, 10)
        await h.stop()
        # The alarm fires: boot time matches the armed wake.
        st = state.load_state(paths.state)
        st.next_wake_utc = time.time() - 4
        state.save_state(paths.state, st)
        h2 = Harness(paths, health_url)
        await h2.start(wait_ready=False)
        try:
            assert h2.app.lifecycle in (machine.WAKE, machine.SHUTTING_DOWN)
            assert ("level", 0) in h2.led.log  # light off during the wake
            await asyncio.wait_for(h2.task, 10)
            assert len(h2.epd.shown) == 1
            assert h2.cam.history == []  # the camera never started
            assert h2.server.values["rtc_alarm_enabled"] == "true"
            assert state.load_state(paths.state).last_shutdown["reason"] == "slideshow"
            assert set(ids)
        finally:
            await h2.stop()

    run(main())


def test_each_photo_keeps_its_print_and_old_ones_are_remade(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        pid = await h.shoot()
        await h.idle_display()
        st = h.app.store
        assert st.print_path(pid).read_bytes().startswith(b"\x89PNG")
        await h.stop()
        # Made with an older palette: the screen image and the print are remade at the next start.
        data = bytearray(st.epd_path(pid).read_bytes())
        data[6:8] = (palette.PALETTE_VERSION - 1).to_bytes(2, "little")
        st.epd_path(pid).write_bytes(bytes(data))
        st.print_path(pid).write_bytes(b"old")
        h2 = Harness(paths, health_url)
        await h2.start()
        try:
            await h2.until(lambda: f"rebuilt screen image for {pid}" in h2.logs)
            assert int.from_bytes(st.epd_path(pid).read_bytes()[6:8], "little") == palette.PALETTE_VERSION
            assert st.print_path(pid).read_bytes().startswith(b"\x89PNG")
            assert "palette changed: rebuilding 1 screen images" in h2.logs
        finally:
            await h2.stop()

    run(main())


def test_share_mode_on_and_off(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            pid = await h.shoot()
            await h.idle_display()
            for tap in ("single", "double"):  # a brush against BTN doesn't start sharing
                await h.server.tap(tap)
                await h.until(lambda line=f"button: BTN {tap}": line in h.logs)
            assert h.app.share == "off"
            await h.server.tap("long")
            await h.until(lambda: h.app.share == "on")
            assert h.app.share_info.mode == "home" and h.steady() in ("share", "wait")
            await h.until(lambda: h.app.state.display_content == "share")
            await h.idle_display()
            st = await h.cmd("status")
            assert st["share"]["mode"] == "home" and st["share"]["timeout_min"] == 10
            await h.server.tap("single")
            await h.until(lambda: h.logs.count("button: BTN single") == 2)
            assert h.app.share == "on"
            await h.server.tap("long")
            await h.until(lambda: h.app.share == "off")
            await h.idle_display()
            assert h.app.state.display_content == "photo" and h.app.state.current == pid
            assert h.net.state == "off" and h.net.log[-2:] == ["down", "radio False"]
        finally:
            await h.stop()

    run(main())


def test_dev_mode_sharing_at_home_keeps_the_home_wifi(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(cfg={"dev_mode": True})
        h.net.state = "inkstant-home-1"  # NetworkManager joined it at boot
        scans = []
        real_scan = h.net.scan
        h.net.scan = lambda: scans.append(1) or real_scan()
        try:
            await h.server.tap("long")
            await h.until(lambda: h.app.share == "on")
            assert h.app.share_info.mode == "home"
            assert scans == []  # already joined: no Wi-Fi scan to wait for
            await h.until(lambda: h.app.state.display_content == "share")  # BTN is ignored mid-refresh
            await h.idle_display()
            await h.server.tap("long")
            await h.until(lambda: h.app.share == "off")
            # Still joined, never dropped: SSH keeps working, and NetworkManager isn't left blocking it.
            assert h.net.state == "inkstant-home-1"
            assert not any(e.startswith(("up", "down", "radio False")) for e in h.net.log)
        finally:
            await h.stop()

    run(main())


def test_dev_mode_hotspot_stop_leaves_the_radio_on(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False, cfg={"dev_mode": True})
        h.net.state = "inkstant-home-1"
        try:
            await h.cmd("share.start", force_hotspot=True)
            assert h.app.share_info.mode == "hotspot"
            await h.cmd("share.stop")
            await h.until(lambda: h.app.share == "off")
            # The hotspot goes down; NetworkManager then rejoins inkstant-home-1 by itself.
            assert h.net.state == "off" and h.net.log[-1] == "down"
            assert "radio False" not in h.net.log
        finally:
            await h.stop()

    run(main())


def test_double_tap_while_sharing_switches_between_home_and_camera_wifi(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            await h.idle_display()
            await h.server.tap("long")
            await h.until(lambda: h.app.share == "on")
            assert h.app.share_info.mode == "home"
            await h.until(lambda: h.app.state.display_content == "share")
            await h.idle_display()
            await h.server.tap("double")
            await h.until(lambda: h.app.share == "on" and h.app.share_info.mode == "hotspot")
            assert h.app.share_info.url == "http://10.42.0.1/"
            assert h.app.share_info.chosen
            await h.until(lambda: h.logs.count("display: SHARE_SCREEN(share)") == 2)  # the camera's QR codes
            await h.idle_display()
            await h.server.tap("double")  # and back
            await h.until(lambda: h.app.share == "on" and h.app.share_info.mode == "home")
            await h.until(lambda: h.logs.count("display: SHARE_SCREEN(share)") == 3)
            assert h.logs.count("share: switching to the camera Wi-Fi") == 1
            assert h.logs.count("share: switching to home Wi-Fi") == 1
        finally:
            await h.stop()

    run(main())


def test_double_tap_on_the_camera_wifi_with_no_home_wifi_saved_does_nothing(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False)
        h.net.home = []
        try:
            await h.server.tap("long")
            await h.until(lambda: h.app.share == "on")
            assert h.app.share_info.mode == "hotspot" and not h.app.share_info.chosen
            await h.server.tap("double")
            await h.until(lambda: "button: BTN double" in h.logs)
            await asyncio.sleep(0.1)
            assert h.app.share == "on" and not any(line.startswith("share: switching") for line in h.logs)
        finally:
            await h.stop()

    run(main())


def test_a_qr_code_left_on_the_print_is_replaced_at_start(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        pid = await h.shoot()
        await h.idle_display()
        await h.server.tap("long")
        await h.until(lambda: h.app.state.display_content == "share")
        await h.idle_display()
        await h.stop()  # sharing ends with the camera; the print keeps the QR code
        assert state.load_state(paths.state).display_content == "share"
        h2 = Harness(paths, health_url)
        await h2.start()
        try:
            await h2.until(lambda: h2.app.state.display_content == "photo")
            assert h2.app.state.current == pid
        finally:
            await h2.stop()

    run(main())


def test_a_failed_switch_to_the_camera_wifi_takes_the_qr_code_down(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            await h.idle_display()
            await h.server.tap("long")
            await h.until(lambda: h.app.state.display_content == "share")
            await h.idle_display()
            h.net.up_hotspot = lambda: False
            await h.server.tap("double")
            await h.until(lambda: h.app.share == "off" and h.app.state.display_content != "share")
            assert "share: couldn't start the camera's Wi-Fi" in h.logs
        finally:
            await h.stop()

    run(main())


def test_share_timeout_on_battery(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False)
        try:
            await h.cmd("share.start")
            await h.until(lambda: h.app.share == "on")
            h.clock.advance(10 * 60 + 1)
            await h.app.tick()
            await h.until(lambda: h.app.share == "off")
            assert h.app.lifecycle == machine.ACTIVE  # turn-off restarts from now
        finally:
            await h.stop()

    run(main())


def test_share_start_outlives_a_request_that_gives_up(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False)
        try:
            gate = threading.Event()
            scan = h.net.scan
            h.net.scan = lambda: (gate.wait(5), scan())[1]
            req = asyncio.ensure_future(h.cmd("share.start"))
            await h.until(lambda: h.app.share == "starting")
            req.cancel()  # what the control socket's timeout does to a slow request
            gate.set()
            await h.until(lambda: h.app.share == "on")
            assert h.app.share_info.mode == "home"
            await h.cmd("share.stop")
            await h.until(lambda: h.app.share == "off")
            assert await h.cmd("share.start") == {"mode": "home", "url": "http://127.0.0.1/", "ssid": "Sim Home"}
            assert ipc.timeout_for("share.start") > ipc.REQUEST_TIMEOUT
        finally:
            await h.stop()

    run(main())


def test_stopping_during_a_start_waits_for_it(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False)
        try:
            gate = threading.Event()
            scan = h.net.scan
            h.net.scan = lambda: (gate.wait(5), scan())[1]
            start = asyncio.ensure_future(h.app.share_start())
            await h.until(lambda: h.app.share == "starting")
            stop = asyncio.ensure_future(h.app.share_stop())
            await asyncio.sleep(0.2)
            assert h.app.share == "starting"  # the stop waits instead of racing the start
            gate.set()
            await asyncio.wait_for(asyncio.gather(start, stop), 5)
            assert h.app.share == "off" and h.net.state == "off"
        finally:
            await h.stop()

    run(main())


def test_update_check_before_any_release(paths, health_url, monkeypatch):
    from inkstant.camera_app import update

    def no_release(req, timeout):
        raise urllib.error.HTTPError(update.LATEST_URL, 404, "Not Found", {}, None)

    monkeypatch.setattr(update.urllib.request, "urlopen", no_release)
    with pytest.raises(update.NoRelease):
        update.check_latest()
    monkeypatch.undo()  # the app's gallery health check uses urlopen too

    def latest():
        raise update.NoRelease("no release published yet")

    monkeypatch.setattr(update, "check_latest", latest)

    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False)
        try:
            await h.cmd("share.start")
            r = await h.cmd("update.check")
            assert r["available"] is False and r["latest"] is None
        finally:
            await h.stop()

    run(main())


def test_storage_full_refuses_shutter(paths, health_url, monkeypatch):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            monkeypatch.setattr(h.app.store, "disk", lambda: (100 << 20, 10 << 30))
            await h.press()
            await asyncio.sleep(0.2)
            assert h.app.store.list() == [] and h.steady() == "problem"
            assert ("level", 0) not in h.led.log  # no wink
            st = await h.cmd("status")
            assert st["storage"]["state"] == "full"
        finally:
            await h.stop()

    run(main())


def test_settings_and_control_errors(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            with pytest.raises(ipc.IpcError) as e:
                await h.cmd("settings.set", auto_off_min=0)
            assert e.value.code == "invalid"
            r = await h.cmd("settings.set", sleep_min=2, slideshow={"interval_min": 15})
            assert r["settings"]["sleep_min"] == 2
            assert json.loads(paths.settings.read_text())["slideshow"]["interval_min"] == 15
            with pytest.raises(ipc.IpcError) as e:
                await h.cmd("photos.show", id="000999")
            assert e.value.code == "not_found"
            with pytest.raises(ipc.IpcError) as e:
                await h.cmd("photos.show", nonsense=1)
            assert e.value.code == "invalid"
            r = await h.cmd("wifi.home.set", ssid="Cafe", password="secret123")
            assert r["n"] == 2
            assert [x["ssid"] for x in (await h.cmd("wifi.list"))["home"]] == ["Sim Home", "Cafe"]
        finally:
            await h.stop()

    run(main())


def test_delete_clears_undeveloped(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False)
        try:
            pid = await h.shoot()
            r = await h.cmd("photos.delete", ids=[pid, "000777"])
            assert r == {"deleted": [pid], "missing": ["000777"]}
            assert h.app.state.undeveloped is None
        finally:
            await h.stop()

    run(main())


def test_update_defers_power_button(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start()
        try:
            await h.cmd("update.report", state="running", step="installing")
            assert h.steady() == "wait"
            await h.press()
            await asyncio.sleep(0.1)
            assert h.app.store.list() == []  # shutter refused during an update
            r = await h.cmd("power.button")
            assert r["action"] == "deferred" and h.app.lifecycle == machine.ACTIVE
            await h.cmd("shutdown", reason="update", reboot=True)  # update.sh's last step
            await asyncio.wait_for(h.task, 10)
            assert ("poweroff",) in h.sys.calls and ("reboot",) not in h.sys.calls
        finally:
            await h.stop()

    run(main())


def test_camera_failure_shows_problem(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(camera_fail=True)
        try:
            assert h.app.camera_state == "failed" and h.steady() == "problem"
            assert any(e["code"] == "camera" for e in (await h.cmd("status"))["errors"])
        finally:
            await h.stop()

    run(main())


def test_power_on_cues_no_module_and_low_battery(paths, health_url):
    async def main():
        h = Harness(paths, health_url)
        await h.start(module=False, battery="12.0")
        try:
            patterns = [e[1] for e in h.led.log if e[0] == "pattern"]
            assert led.CUES["no_module"][0] in patterns
            await h.until(lambda: led.CUES["battery"][0] in [e[1] for e in h.led.log if e[0] == "pattern"])
        finally:
            await h.stop()

    run(main())
