"""The camera app: one asyncio loop owns all state.

Hardware work runs in threads (display worker, capture worker, a small executor for
nmcli/systemctl/file work); each reports back by scheduling a callback on the loop.
Only the standard library, gpiozero and our own modules are imported at the top, so a
slideshow wake never loads Picamera2, numpy or Pillow.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import signal
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from inkstant import atomic, device, ipc, selftest, settings, state, store, version
from inkstant.camera_app import machine, slideshow
from inkstant.camera_app.capture import CaptureWorker
from inkstant.camera_app.display import BLANK, DEVELOP, SHARE_SCREEN, SHOW, DisplayWorker, Job
from inkstant.camera_app.machine import ACTIVE, SHUTTING_DOWN, STARTING, WAKE
from inkstant.hw import led as led_mod
from inkstant.hw.network import HOME_PREFIX
from inkstant.hw.pisugar import PiSugarClient, PiSugarError, ensure_settings
from inkstant.imaging import epdfile, palette
from inkstant.paths import Paths

GALLERY_UNIT = "inkstant-gallery.service"
DEV_HOME = f"{HOME_PREFIX}1"  # the home network developer mode joins at every start
CTL_COMMAND = "/usr/local/bin/inkstant-ctl power-button"
INSERT_SETTLE = 0.3
WAKE_TIMEOUT = 120.0
SHUTDOWN_DISPLAY_WAIT = 30.0
SHARE_START_WAIT = 90.0
TAP_ECHO = 0.5  # s: the same BTN tap read by both the camera and pisugar-server
ERROR_TEXT = {
    "camera": "Camera problem",
    "display": "Display module not responding",
    "storage": "Couldn't save the photo",
    "storage_full": "Storage full",
    "pisugar": "Battery board not responding",
    "clock": "Set the time from your phone",
    "setup": "Setup check found a problem",
}


@dataclass
class Hardware:
    """Everything the app touches, real or fake."""

    pins: Any
    led_writer: Any
    epd: Any
    camera_factory: Any
    network: Any
    system: Any
    watchdog: Any
    taps: Any = None  # BTN taps read straight from the PiSugar; None: only pisugar-server's
    gallery_health_url: str = "http://127.0.0.1/healthz"
    start_gallery: Any = None  # None: systemctl start the gallery unit
    stop_gallery: Any = None
    trust_all_callers: bool = False
    is_3b: bool = False


def local_tz_name() -> str:
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        return Path("/etc/timezone").read_text().strip() or "UTC"
    except OSError:
        return time.tzname[0] or "UTC"


@dataclass
class ShareInfo:
    mode: str | None = None  # home or hotspot
    url: str | None = None
    network: str | None = None
    chosen: bool = False  # hotspot asked for (a double tap), not a fallback for no home Wi-Fi


@dataclass
class Errors:
    items: dict[str, tuple[str, float]] = field(default_factory=dict)

    def set(self, code: str, message: str | None = None) -> None:
        if code not in self.items:
            self.items[code] = (message or ERROR_TEXT.get(code, code), time.time())
        elif message:
            self.items[code] = (message, self.items[code][1])

    def clear(self, code: str) -> None:
        self.items.pop(code, None)

    def codes(self) -> set[str]:
        return set(self.items)

    def payload(self) -> list[dict]:
        return [{"code": c, "message": m, "since": datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")}
                for c, (m, t) in self.items.items()]


class CameraApp:
    def __init__(self, paths: Paths, hw: Hardware, log=print, clock=time.monotonic):
        self.paths = paths
        self.hw = hw
        self.log = log
        self.clock = clock
        self.device = device.load(paths.device_toml)
        for e in self.device.errors:
            log(e)
        self.settings = settings.load(paths.settings, log)
        self.state = state.load_state(paths.state, log)
        self.slides = state.load_slideshow(paths.slideshow)
        self.store = store.PhotoStore(paths, web_group=store.WEB_GROUP if os.geteuid() == 0 else None)
        self.io = ThreadPoolExecutor(max_workers=2, thread_name_prefix="io")
        self.capture = CaptureWorker(hw.camera_factory, log)
        self.led = led_mod.LedController(hw.led_writer, log=log)
        self.errors = Errors()
        self.control = ipc.ControlServer(paths.control_sock, self.handle_command,
                                         trust_all=hw.trust_all_callers, log=log)
        self.pisugar: PiSugarClient | None = None

        # State
        self.lifecycle = STARTING
        self.module_present = False
        self.camera_state = "off"
        self.capture_busy = False
        self.display = "idle"
        self.display_job: Job | None = None
        self.pending_job: Job | None = None
        self.share = "off"
        self.share_info = ShareInfo()
        self.share_activity = 0.0
        self.battery_readings: list[float] = []
        self.battery_level: float | None = None
        self.plugged = False
        self.battery_low = False
        self.low_count = 0
        self.storage = "ok"
        self.clock_valid = False
        self.boot_utc = time.time()
        self.update_running = False
        self.update_status: dict = {"state": "idle", "step": None, "message": None}
        self.power_after_update = False
        self.power_button_pending = False
        self.shutdown_display_busy = False
        self.last_use = clock()
        self.last_camera_use = clock()
        self.wake_deadline: float | None = None
        self.wake_armed = False
        self.rebuild_queue: list[str] = []
        self.selftest_result: dict | None = None
        self.camera_retry_at: float | None = None

        self._insert_handle: asyncio.TimerHandle | None = None
        self._shutdown_task: asyncio.Task | None = None
        self._stopped: asyncio.Event | None = None
        self._tasks: set[asyncio.Task] = set()
        self._last_feed = 0.0
        self._last_battery = -1e9
        self._last_plug_poll = -1e9
        self._last_storage = -1e9
        self._rebuild_task: asyncio.Task | None = None
        self.display_worker = DisplayWorker(hw.epd, self._post_display, rotation=self.device.display_rotation, log=log)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.external_stop = False
        self._watchdog_off = False
        self._camera_opening = False
        self._taps_stop = threading.Event()
        self._last_tap: tuple[str, str, float] | None = None

    # Plumbing ---------------------------------------------------------------------
    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)

        def done(t):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                self.log(f"task failed: {t.exception()!r}")

        task.add_done_callback(done)
        return task

    async def in_io(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.io, fn, *args)

    def save_state(self) -> None:
        try:
            state.save_state(self.paths.state, self.state, tmp_dir=self.paths.tmp)
        except OSError as e:
            self.log(f"state: save failed: {e}")

    def save_slides(self) -> None:
        with contextlib.suppress(OSError):
            state.save_slideshow(self.paths.slideshow, self.slides, tmp_dir=self.paths.tmp)

    def uptime_mark(self, what: str) -> None:
        self.log(f"timing: {what} at uptime {self.hw.system.uptime():.1f} s")

    # Derived ----------------------------------------------------------------------------
    @property
    def saver(self) -> bool:
        return machine.saver_on(self.settings, self.state.saver_auto_active)

    def running_fps(self) -> int:
        from inkstant.hw.camera import CAMERA_FPS, SAVER_FPS

        if self.saver:
            return SAVER_FPS
        return self.device.camera_fps or CAMERA_FPS

    def view(self) -> machine.View:
        return machine.View(
            lifecycle=self.lifecycle, camera=self.camera_state, capture_busy=self.capture_busy,
            display=self.display,
            develop_pending=self.pending_job is not None and self.pending_job.kind == DEVELOP,
            shutdown_display_busy=self.shutdown_display_busy, share=self.share, storage=self.storage,
            update_running=self.update_running)

    def update_led(self) -> None:
        self.led.set_steady(machine.led_steady(self.view()))

    # Startup -----------------------------------------------------------------------------
    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._stopped = asyncio.Event()
        self.uptime_mark("app start")
        self.paths.ensure_dirs()
        self.rebuild_queue = await self.in_io(self.store.recover)
        self.state.next_seq = max(self.state.next_seq, self.store.max_seq() + 1)
        await self.control.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                self.loop.add_signal_handler(sig, self._on_sigterm)

        self.pisugar = PiSugarClient(self.paths.pisugar_sock, on_tap=lambda tap: self.on_tap("pisugar-server", tap),
                                     log=self.log)
        if await self.pisugar.connect():
            problems = await ensure_settings(self.pisugar, CTL_COMMAND, self.log)
            for p in problems:
                self.log(f"pisugar: {p}")
            await self._apply_charge_limit()
            try:
                await self.in_io(self.hw.watchdog.enable)
            except Exception as e:
                self.log(f"watchdog: {e}")
                self.errors.set("pisugar", "Battery board not responding (watchdog)")
        else:
            self.log("pisugar: pisugar-server isn't answering; carrying on without it")
            self.errors.set("pisugar")
        await self.check_clock()
        await self.read_battery(initial=True)

        timed = self.clock_valid and slideshow.is_timed_wake(self.boot_utc, self.state.next_wake_utc)
        if self.state.next_wake_utc is not None:
            self.state.next_wake_utc = None
            self.save_state()
        self.uptime_mark(f"wake decision ({'timed wake' if timed else 'normal start'})")

        self._attach_gpio()
        self._attach_taps()
        self.module_present = self.hw.pins.module_present()
        self.display_worker.start()
        self.hw.system.notify("READY=1")
        tick = self.spawn(self._tick_loop())
        pending, self.power_button_pending = self.power_button_pending, False
        if timed and not pending:
            await self.start_wake()
        elif timed:
            await self.start_active("power button during a slideshow wake")  # PWR means "turn on"
        else:
            await self.start_active()
            if pending:
                self.request_shutdown("power_button")  # a press while starting, in normal use
        await self._stopped.wait()
        tick.cancel()
        await self._teardown()

    def _attach_gpio(self) -> None:
        pins, loop = self.hw.pins, self.loop
        pins.detect.when_activated = lambda: loop.call_soon_threadsafe(self.on_detect, True)
        pins.detect.when_deactivated = lambda: loop.call_soon_threadsafe(self.on_detect, False)

    def _attach_taps(self) -> None:
        if self.hw.taps is None:
            return
        loop = self.loop

        def on_tap(tap: str) -> None:
            with contextlib.suppress(RuntimeError):  # the loop has closed: the app is stopping
                loop.call_soon_threadsafe(self.on_tap, "camera", tap)

        threading.Thread(target=self.hw.taps.run, args=(on_tap, self._taps_stop), name="btn", daemon=True).start()

    async def _teardown(self) -> None:
        self._taps_stop.set()
        with contextlib.suppress(Exception):
            await self.control.close()
        if self.pisugar:
            with contextlib.suppress(Exception):
                await self.pisugar.close()
        self.display_worker.stop()
        self.led.close()
        self.capture.shutdown()
        self.io.shutdown(wait=False, cancel_futures=True)

    def _on_sigterm(self) -> None:
        self.log("signal: stopping")
        self.external_stop = True
        if self._shutdown_task is None:
            self._shutdown_task = self.spawn(self._shutdown("system"))

    # Normal start --------------------------------------------------------------------------
    async def start_active(self, reason: str = "power on") -> None:
        self.lifecycle = ACTIVE
        self.last_use = self.last_camera_use = self.clock()
        self.wake_deadline = None
        if self.camera_state in ("off", "failed"):
            self.camera_state = "starting"  # the light shows "wait" from now
        self.update_led()
        if not self.settings["dev_mode"]:
            with contextlib.suppress(Exception):
                await self.in_io(self.hw.network.radio, False)
            if self.hw.is_3b:
                await self.in_io(self.hw.system.usb_hub_off)
        und = self.state.undeveloped
        showing = self.display_job is not None and self.display_job.photo_id == und
        if self.module_present and und and not showing and self.store.get(und):
            self.queue_job(Job(DEVELOP, und))
        elif self.module_present and not und and self.state.display_content == "share":
            self._restore_display()  # a QR code left by a restart or power cut no longer works
        self.spawn(self.start_camera())
        self.spawn(self._first_start_selftest())
        self.spawn(self.queue_palette_rebuilds())

    async def start_camera(self) -> None:
        if self._camera_opening or self.camera_state not in ("off", "failed", "starting"):
            return
        self._camera_opening = True
        self.camera_state = "starting"
        self.update_led()
        try:
            mode = await self.capture.open()
            self.camera_state = "fallback" if mode == "fallback" else "ready"
            await self.capture.set_fps(self.running_fps())
            self.errors.clear("camera")
            self.camera_retry_at = None
            self.last_camera_use = self.clock()
        except Exception as e:
            why = str(e) or type(e).__name__  # some exceptions, like TimeoutError, have no message
            self.log(f"camera: {why}")
            self.camera_state = "failed"
            self.errors.set("camera", f"Camera problem: {why}")
            self.camera_retry_at = self.clock() + 60
            with contextlib.suppress(Exception):
                await self.capture.close()
        finally:
            self._camera_opening = False
        self.update_led()
        if self.camera_state in ("ready", "fallback"):
            self.uptime_mark("ready")
            self._kick_rebuild()

    # The status light: its steady pattern through update_led(), the shutter's wink through self.led.

    # Events: shutter (a BTN tap, through on_btn) -------------------------------------------------
    def on_shutter(self, press_ns: int, wall: float) -> None:
        now = self.clock()
        self.last_use = now
        if self.lifecycle in (STARTING, SHUTTING_DOWN):
            return
        if self.lifecycle == WAKE:
            self.spawn(self.switch_to_active("shutter"))
            return
        self.refresh_storage()
        if not machine.shutter_accepted(self.view()):
            self.log("shutter: refused")
            self.update_led()
            return
        self.last_camera_use = now
        self.capture_busy = True
        self.led.wink()
        self.update_led()
        taken = datetime.fromtimestamp(wall) if self.clock_valid else None
        self.spawn(self._do_capture(press_ns, taken))

    async def _do_capture(self, press_ns: int, taken: datetime | None) -> None:
        try:
            while self.camera_state == "waking":  # a module insertion is already waking it
                await asyncio.sleep(0.05)
            wake_first = self.camera_state == "asleep"
            if wake_first:
                self.camera_state = "waking"
                self.update_led()
            if self.camera_state not in ("ready", "fallback", "waking"):
                raise RuntimeError(f"camera is {self.camera_state}")
            result = await self.capture.capture(press_ns, taken, wake_first, self.running_fps())
            if wake_first:
                self.camera_state = "ready"
                self.last_camera_use = self.clock()
            seq = max(self.state.next_seq, self.store.max_seq() + 1)
            pid = store.make_id(seq, taken)
            await self.in_io(self.store.commit, pid, result.epd, result.thumb, result.jpeg, result.print_png)
            self.state.next_seq = seq + 1
            self.save_state()
            self.on_photo_committed(pid)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                self.storage = "full"
                self.errors.set("storage_full")
            else:
                self.errors.set("storage", f"Couldn't save the photo: {e.strerror or e}")
            self.log(f"capture: save failed: {e}")
        except Exception as e:
            self.log(f"capture: {e!r}")
            self.errors.set("camera", f"Camera problem: {e}")
            self.camera_state = "failed"
            with contextlib.suppress(Exception):
                await self.capture.close()
            self.camera_state = "off"
            self.spawn(self.start_camera())
        finally:
            self.capture_busy = False
            self.update_led()

    def on_photo_committed(self, pid: str) -> None:
        self.log(f"photo: {pid}")
        self.errors.clear("storage")
        self.state.undeveloped = pid
        self.save_state()
        self.slides.add(store.seq_of(pid))
        self.save_slides()
        self.refresh_storage()
        if self.module_present and self.share == "off":
            self.queue_job(Job(DEVELOP, pid))
        elif not self.module_present:
            self.capture_busy = False
            self.update_led()

    # Events: module ---------------------------------------------------------------------------
    def on_detect(self, present: bool) -> None:
        if present:
            if self._insert_handle:
                self._insert_handle.cancel()
            self._insert_handle = self.loop.call_later(INSERT_SETTLE, self._confirm_insert)
        else:
            if self._insert_handle:
                self._insert_handle.cancel()
                self._insert_handle = None
            if self.module_present:
                self.on_module_removed()

    def _confirm_insert(self) -> None:
        self._insert_handle = None
        if self.hw.pins.module_present() and not self.module_present:
            self.on_module_inserted()

    def on_module_removed(self) -> None:
        self.log("module: removed")
        self.module_present = False
        self.last_use = self.last_camera_use = self.clock()
        self.pending_job = None
        if self.display_job is not None:
            self.display_worker.abort()
        self.state.display_content = "unknown"
        self.save_state()
        self.errors.clear("display")
        self.update_led()

    def on_module_inserted(self) -> None:
        self.log("module: inserted")
        self.module_present = True
        self.last_use = self.last_camera_use = self.clock()
        if self.lifecycle == STARTING:
            return
        if self.lifecycle == SHUTTING_DOWN:
            return
        if self.lifecycle == WAKE:
            if self.display_job is None and not self.wake_armed:
                self.spawn(self._wake_show())
            return
        if self.camera_state == "asleep":
            self.spawn(self._wake_camera())
        if self.share == "on":
            self.spawn(self._queue_share_screen())
        elif self.state.undeveloped and self.store.get(self.state.undeveloped):
            self.queue_job(Job(DEVELOP, self.state.undeveloped))
        self.update_led()

    # Events: buttons ----------------------------------------------------------------------------
    def on_tap(self, reader: str, tap: str) -> None:
        """A BTN tap, read by the camera itself (quick) or by pisugar-server (every 500 ms).
        Whichever reads it first clears it, but both can read it before either clears it: the
        second copy, from the other reader moments later, is dropped."""
        now = self.clock()
        last = self._last_tap
        if last and last[0] != reader and last[1] == tap and now - last[2] < TAP_ECHO:
            self._last_tap = None
            self.log(f"button: BTN {tap} again from {reader}; dropped")
            return
        self._last_tap = (reader, tap, now)
        if reader == "pisugar-server" and self.hw.taps is not None:
            self.log(f"button: BTN {tap} reached pisugar-server first")
        self.on_btn(tap)

    def on_btn(self, tap: str) -> None:
        """BTN is the shutter: a tap takes a photo, and so does a double tap unless sharing. A hold
        starts or stops sharing. A double tap while sharing switches between home Wi-Fi and the
        camera's own, as the share screen says."""
        self.last_use = self.clock()
        self.log(f"button: BTN {tap}")
        if tap == "single" or (tap == "double" and self.share == "off"):
            self.on_shutter(time.monotonic_ns(), time.time())
            return
        if self.lifecycle in (STARTING, SHUTTING_DOWN):
            return
        if self.lifecycle == WAKE:
            if tap == "long":
                self.spawn(self.switch_to_active("button", share=True))
            return
        if not machine.btn_accepted(self.view()):
            return
        if tap == "long":
            if self.share == "on":
                self.spawn(self.share_stop())
            elif self.share == "off":
                self.spawn(self.share_start())
        elif tap == "double" and self.share == "on":
            self.spawn(self.share_switch())

    def power_button(self) -> str:
        """`inkstant-ctl power-button` (run by pisugar-server). Must answer at once: pisugar-server waits for it."""
        if self.lifecycle == STARTING:
            self.power_button_pending = True
            return "deferred"
        if self.lifecycle == WAKE:
            action = machine.power_button_in_wake(self.battery_level, self.plugged)
            if action == "shutdown":
                self.request_shutdown("low_battery")
                return "shutdown"
            self.spawn(self.switch_to_active("power"))
            return "turn_on"
        if self.lifecycle == SHUTTING_DOWN:
            return "ignored"
        if self.update_running:
            self.power_after_update = True
            return "deferred"
        self.request_shutdown("power_button")
        return "shutdown"

    # Display jobs ---------------------------------------------------------------------------------
    def queue_job(self, job: Job) -> str:
        if not self.module_present:
            return "no_module"
        if job.kind in (DEVELOP, SHOW):
            job.epd_path = self.store.epd_path(job.photo_id)
            if not job.epd_path.exists():
                self.log(f"display: no screen image for {job.photo_id}; rebuilding")
                self._queue_rebuild(job.photo_id)
                return "missing"
        if self.display_job is None:
            self._start_job(job)
            return "now"
        self.pending_job = job
        self.update_led()
        return "next"

    def _start_job(self, job: Job) -> None:
        self.display_job = job
        self.display = "refreshing"
        self.log(f"display: {job.describe()}")
        self.display_worker.submit(job)
        self.update_led()

    def _post_display(self, name: str, job: Job, detail) -> None:
        self.loop.call_soon_threadsafe(self._on_display_event, name, job, detail)

    def _on_display_event(self, name: str, job: Job, detail) -> None:
        if job is not self.display_job:
            return
        if name == "display_shown":
            self.display = "tail"
            self.update_led()
            if self.lifecycle == WAKE:
                self.spawn(self._wake_after_shown())
            return
        # finished
        result = detail
        if result == "ok":
            self.errors.clear("display")
            if job.kind in (DEVELOP, SHOW):
                self.state.current = job.photo_id
                if self.state.undeveloped == job.photo_id:
                    self.state.undeveloped = None
                self.state.display_content = "photo"
            elif job.kind == SHARE_SCREEN:
                self.state.display_content = "share"
            elif job.kind == BLANK:
                self.state.current = None
                self.state.display_content = "blank"
            self.save_state()
        elif result in ("timeout", "missing", "error"):
            if self.hw.pins.module_present():
                if result != "missing" or self.lifecycle != SHUTTING_DOWN:
                    self.errors.set("display")
            else:
                self.module_present = False
        elif result == "unreadable" and job.photo_id:
            self._queue_rebuild(job.photo_id)
        self.display_job = None
        self.display = "idle"
        nxt, self.pending_job = self.pending_job, None
        if nxt is not None and self.module_present and self.lifecycle != SHUTTING_DOWN:
            self._start_job(nxt)
        self.update_led()
        if self.lifecycle == WAKE and nxt is None:
            self.spawn(self._wake_after_display())

    async def wait_display_idle(self, timeout: float) -> bool:
        deadline = self.clock() + timeout
        while self.display_job is not None:
            if self.clock() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    # Sleep ----------------------------------------------------------------------------------
    async def _sleep_camera(self) -> None:
        if self.camera_state not in ("ready", "fallback") or self.capture_busy:
            return
        self.log("camera: asleep")
        try:
            await self.capture.sleep()
            self.camera_state = "asleep"
        except Exception as e:
            self.log(f"camera: sleep failed: {e}")
        self.update_led()

    async def _wake_camera(self) -> None:
        if self.camera_state != "asleep":
            return
        self.camera_state = "waking"
        self.update_led()
        try:
            await self.capture.wake()
            await self.capture.set_fps(self.running_fps())
            self.camera_state = "ready"
            self.last_camera_use = self.clock()
        except Exception as e:
            self.log(f"camera: wake failed: {e}")
            self.errors.set("camera", f"Camera problem: {e}")
            self.camera_state = "failed"
            self.camera_retry_at = self.clock() + 60
        self.update_led()

    # Timers --------------------------------------------------------------------------------
    async def _tick_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:
                self.log(f"tick: {e!r}")
            await asyncio.sleep(1.0)

    async def tick(self) -> None:
        now = self.clock()
        if now - self._last_feed >= 10:
            self._last_feed = now
            self.hw.system.notify("WATCHDOG=1")
            if not self._watchdog_off:
                with contextlib.suppress(Exception):
                    await self.in_io(self.hw.watchdog.feed)
        if self.lifecycle == SHUTTING_DOWN:
            return
        if self.pisugar is not None:
            if now - self._last_battery >= 60:
                self._last_battery = self._last_plug_poll = now
                await self.read_battery()
            elif now - self._last_plug_poll >= 10:
                self._last_plug_poll = now
                await self.poll_plug()
        if self.lifecycle == WAKE:
            if self.wake_deadline is not None and now >= self.wake_deadline:
                self.request_shutdown("wake_timeout")
            return
        if self.lifecycle != ACTIVE:
            return
        if now - self._last_storage >= 600:
            self._last_storage = now
            self.refresh_storage()
        sleep_min, off_min, share_min = machine.effective_minutes(self.settings, self.saver)
        if (sleep_min is not None and self.camera_state in ("ready", "fallback") and not self.capture_busy
                and now - self.last_camera_use >= sleep_min * 60):
            await self._sleep_camera()
        paused = (self.share != "off" or self.update_running
                  or (self.settings["dev_mode"] and self.plugged))
        if paused:
            self.last_use = max(self.last_use, now - 1)  # resumes from the moment it's unpaused
        elif self.display_job is None and not self.capture_busy and now - self.last_use >= off_min * 60:
            self.log("auto-off")
            self.request_shutdown("auto_off")
            return
        if (self.share == "on" and not self.plugged and not self.update_running
                and now - self.share_activity >= share_min * 60):
            self.log("share: timed out")
            await self.share_stop()
        if self.camera_state == "failed" and self.camera_retry_at and now >= self.camera_retry_at:
            self.camera_state = "off"
            self.spawn(self.start_camera())

    # Battery and saver -----------------------------------------------------------------
    async def poll_plug(self) -> None:
        try:
            plugged = await self.pisugar.plugged()
        except PiSugarError:
            return
        if plugged != self.plugged:
            await self.read_battery()

    async def read_battery(self, initial: bool = False) -> None:
        if not self.pisugar:
            return
        try:
            level = await self.pisugar.battery()
            plugged = await self.pisugar.plugged()
            self.errors.clear("pisugar")
        except PiSugarError as e:
            self.log(f"battery: {e}")
            self.errors.set("pisugar")
            return
        if plugged != self.plugged and not initial:
            self.log(f"battery: {'plugged in' if plugged else 'unplugged'}")
        self.plugged = plugged
        self.battery_readings = (self.battery_readings + [level])[-3:]
        self.battery_level = machine.median(self.battery_readings)
        levels = machine.battery_levels(self.settings)
        self.battery_low = (not plugged) and self.battery_level is not None and self.battery_level <= levels.warn
        before = (self.state.saver_auto_active, self.state.saver_auto_dismissed)
        after = machine.saver_auto_update(self.settings, self.battery_level, plugged, *before)
        if after != before:
            self.state.saver_auto_active, self.state.saver_auto_dismissed = after
            self.save_state()
            self.log(f"battery saver: {'on (automatic)' if after[0] else 'automatic saver off'}")
            await self.apply_saver()
        if not plugged and self.battery_level is not None and self.battery_level <= levels.shutdown:
            self.low_count += 1
            if self.low_count >= 2 and self.lifecycle in (ACTIVE, WAKE):
                self.log(f"battery: {self.battery_level:.0f}%: shutting down")
                self.request_shutdown("low_battery")
        else:
            self.low_count = 0

    async def apply_saver(self) -> None:
        if self.camera_state in ("ready", "fallback"):
            with contextlib.suppress(Exception):
                await self.capture.set_fps(self.running_fps())
        elif self.camera_state == "asleep" and self.capture.camera is not None:
            self.capture.camera.fps = self.running_fps()

    async def _apply_charge_limit(self) -> None:
        if not self.pisugar:
            return
        want = "true" if self.settings["charge_limit"] else "false"
        try:
            if (await self.pisugar.get("battery_input_protect_enabled")) != want:
                await self.pisugar.set("set_battery_input_protect", want)
        except PiSugarError as e:
            self.log(f"charge limit: {e}")

    # Storage ----------------------------------------------------------------------------------
    def refresh_storage(self) -> None:
        try:
            free, _ = self.store.disk()
        except OSError:
            return
        new = self.store.storage_state(free)
        if new == "full":
            self.errors.set("storage_full")
        else:
            self.errors.clear("storage_full")
        if new != self.storage:
            self.storage = new
            self.update_led()

    # Clock ---------------------------------------------------------------------------------------
    async def check_clock(self) -> None:
        sysm = self.hw.system
        rtc = None
        via_kernel = sysm.rtc0_is_pisugar() and self.device.family != "pi5"
        if via_kernel:
            rtc = sysm.rtc0_epoch()
        if rtc is None and self.pisugar and self.pisugar.connected:
            try:
                rtc = (await self.pisugar.rtc_time()).timestamp()
                via_kernel = False
            except (PiSugarError, ValueError) as e:
                self.log(f"clock: {e}")
        self.clock_valid = rtc is not None and rtc >= version.BUILD_EPOCH
        if self.clock_valid:
            self.errors.clear("clock")
            if abs(time.time() - rtc) > 2:
                try:
                    if via_kernel:
                        sysm.set_clock(float(rtc))
                    else:
                        await self.pisugar.rtc_to_pi()
                except Exception as e:
                    self.log(f"clock: couldn't set the system clock: {e}")
            self.boot_utc = rtc - sysm.uptime()
        else:
            self.log("clock: the PiSugar's clock is unset")
            self.errors.set("clock")
            self.boot_utc = time.time() - sysm.uptime()

    async def set_time(self, epoch_ms: int, tz: str | None) -> None:
        if tz:
            ok = await self.in_io(self.hw.system.set_timezone, tz)
            if not ok:
                self.log(f"time: unknown time zone {tz!r}")
        await self.in_io(self.hw.system.set_clock, epoch_ms / 1000)
        if self.pisugar:
            with contextlib.suppress(PiSugarError):
                await self.pisugar.pi_to_rtc()
        self.clock_valid = True
        self.errors.clear("clock")
        self.log("time: set from a phone")

    # Slideshow wake -----------------------------------------------------------------------------
    async def start_wake(self) -> None:
        self.lifecycle = WAKE
        self.update_led()
        self.wake_deadline = self.clock() + WAKE_TIMEOUT
        levels = machine.battery_levels(self.settings)
        if not self.plugged and self.battery_level is not None and self.battery_level <= levels.shutdown:
            self.request_shutdown("low_battery")
            return
        if self.module_present:
            await self._wake_show()

    def _wake_photo(self) -> str | None:
        if self.state.undeveloped and self.store.get(self.state.undeveloped):
            return self.state.undeveloped
        photos = self.store.list()
        seqs = [p.seq for p in photos if self.store.epd_path(p.id).exists()]
        seq = self.slides.next(seqs, self.settings["slideshow"]["order"])
        self.save_slides()
        if seq is None:
            return None
        p = self.store.by_seq(seq)
        return p.id if p else None

    async def _wake_show(self) -> None:
        pid = self._wake_photo()
        if pid is None:
            self.request_shutdown("slideshow")
            return
        kind = DEVELOP if pid == self.state.undeveloped else SHOW
        if self.queue_job(Job(kind, pid, why="slideshow")) == "missing":
            self.request_shutdown("slideshow")

    async def _wake_after_shown(self) -> None:
        """Arm the next wake while the panel powers down (saves 1 to 2 s per wake)."""
        if self.lifecycle == WAKE and not self.wake_armed:
            await self.arm_next_wake()
            self.save_state()

    async def _wake_after_display(self) -> None:
        if self.lifecycle == WAKE:
            self.request_shutdown("slideshow")

    async def switch_to_active(self, trigger: str, share: bool = False) -> None:
        if self.lifecycle != WAKE:
            return
        self.log(f"wake: switching to normal use ({trigger})")
        self.wake_armed = False
        await self.start_active()
        if share:
            await self.share_start()

    async def arm_next_wake(self) -> float | None:
        has_photos = bool(self.store.list())
        t = slideshow.next_wake(time.time(), self.settings, self.battery_level, self.plugged,
                                self.clock_valid, has_photos)
        if not self.pisugar or not self.pisugar.connected:
            return t
        try:
            if t is None:
                await self.pisugar.alarm_disable()
                self.state.next_wake_utc = None
            else:
                when = datetime.fromtimestamp(t).astimezone()
                await self.pisugar.alarm_set(when)
                enabled, armed = await self.pisugar.alarm_read()
                self.log(f"slideshow: next wake {when.isoformat(timespec='seconds')} (read back {enabled}, {armed})")
                self.state.next_wake_utc = t
            self.wake_armed = True
        except PiSugarError as e:
            self.log(f"slideshow: couldn't arm the wake: {e}")
            self.state.next_wake_utc = None
        return t

    # Share mode ----------------------------------------------------------------------------------------
    async def share_start(self, force_hotspot: bool = False) -> str | None:
        """Start sharing; returns why it couldn't, or None."""
        if self.share != "off" or self.lifecycle != ACTIVE:
            return None
        self.share = "starting"
        self.share_activity = self.clock()
        self.update_led()
        net = self.hw.network
        info = ShareInfo()
        t0 = self.clock()
        # The gallery listens on every address, so it starts while Wi-Fi comes up.
        gallery = asyncio.create_task(self._start_gallery())
        try:
            await self.in_io(net.radio, True)
            await self.in_io(net.wait_wlan0, 10)
            if not force_hotspot:
                homes = await self.in_io(net.homes)
                active = await self.in_io(net.active_profile)
                joined = next((h for h in homes if active == f"{HOME_PREFIX}{h.n}"), None)
                if joined:
                    # Already on it (developer mode keeps it for SSH): no scan, and bringing it up
                    # again would drop SSH for a moment.
                    ip = await self.in_io(net.address)
                    if ip:
                        info = ShareInfo("home", f"http://{ip}/", joined.ssid)
                if info.mode is None and homes:
                    visible = await self.in_io(net.scan)
                    for h in sorted((h for h in homes if h.ssid in visible), key=lambda h: -visible[h.ssid].signal):
                        # Networks are saved as WPA2 (`wpa-psk`); one that's WPA3 only needs `sae`, and back
                        # again if the router changes. An open network, or one saved without a password, stays.
                        want = visible[h.ssid].key_mgmt
                        if (want and h.key_mgmt in ("wpa-psk", "sae") and want != h.key_mgmt
                                and await self.in_io(net.set_home_key_mgmt, h.n, want)):
                            self.log(f"share: {HOME_PREFIX}{h.n} now uses {want}")
                        if await self.in_io(net.up, f"{HOME_PREFIX}{h.n}", 20):
                            ip = await self.in_io(net.address)
                            if ip:
                                info = ShareInfo("home", f"http://{ip}/", h.ssid)
                                break
            if info.mode is None:
                if not await self.in_io(net.up_hotspot):
                    raise RuntimeError("couldn't start the camera's Wi-Fi")
                info = ShareInfo("hotspot", "http://10.42.0.1/", self.settings["hotspot"]["ssid"], force_hotspot)
            if not await gallery:
                raise RuntimeError("the gallery didn't start")
        except Exception as e:
            with contextlib.suppress(Exception):
                await gallery  # let it finish starting, so stopping it below doesn't race
            self.log(f"share: {e}")
            self.errors.set("wifi", f"Couldn't start sharing: {e}")
            self.share = "on"  # so share_stop tears everything down
            # Develops a photo saved while this was starting (pressed just before), if there is one.
            await self.share_stop()
            self.errors.clear("wifi")
            self.update_led()
            return str(e)
        self.share_info = info
        self.share = "on"
        self.share_activity = self.clock()
        self.log(f"share: on ({info.mode}) at {info.url}")
        self.log(f"timing: sharing started in {self.clock() - t0:.1f} s")
        self.update_led()
        if self.module_present:
            await self._queue_share_screen()
        if info.mode == "home" and self.pisugar:
            self.spawn(self._internet_time())
        return None

    async def _internet_time(self) -> None:
        try:
            await self.pisugar.rtc_web()
            await asyncio.sleep(5)
            await self.check_clock()
        except PiSugarError as e:
            self.log(f"clock: rtc_web: {e}")

    async def _queue_share_screen(self) -> None:
        info = self.share_info
        if info.mode is None:
            return

        # Going back to home Wi-Fi only works if there is one.
        home_hint = info.chosen and bool(await self.in_io(self.hw.network.homes))

        def render():
            from inkstant.imaging import share_screen

            if info.mode == "home":
                return share_screen.frame_home(info.url, info.network or "")
            hs = self.settings["hotspot"]
            return share_screen.frame_away(hs["ssid"], hs["password"], info.url, home_hint=home_hint)

        try:
            frame = await self.capture.run(render)
        except Exception as e:
            self.log(f"share screen: {e!r}")
            return
        if self.share == "on":
            self.queue_job(Job(SHARE_SCREEN, frame=frame, why="share"))

    async def _start_gallery(self) -> bool:
        if self.hw.start_gallery is not None:
            await self.in_io(self.hw.start_gallery)
        else:
            await self.in_io(self.hw.system.start_unit, GALLERY_UNIT)

        def healthy() -> bool:
            try:
                with urllib.request.urlopen(self.hw.gallery_health_url, timeout=1) as r:
                    return r.status == 200
            except OSError:
                return False

        deadline = self.clock() + 10
        while self.clock() < deadline:
            if await self.in_io(healthy):
                return True
            await asyncio.sleep(0.2)
        return False

    async def share_stop(self, restore: bool = True) -> None:
        # A start in progress finishes first (it can take a minute); stopping halfway through
        # would race it and leave the gallery or the camera's Wi-Fi running.
        deadline = self.clock() + SHARE_START_WAIT
        while self.share == "starting" and self.clock() < deadline:
            await asyncio.sleep(0.1)
        if self.share not in ("on", "starting"):
            return
        self.share = "stopping"
        self.update_led()
        net = self.hw.network
        try:
            if self.hw.stop_gallery is not None:
                await self.in_io(self.hw.stop_gallery)
            else:
                await self.in_io(self.hw.system.stop_unit, GALLERY_UNIT)
        except Exception as e:
            self.log(f"share: stopping the gallery: {e}")
        dev = self.settings["dev_mode"]
        with contextlib.suppress(Exception):
            # Developer mode keeps its home network up for SSH. `nmcli connection down` would
            # also stop NetworkManager reconnecting it until a reboot (nmcli(1)). After the hotspot or
            # another home network goes down, NetworkManager rejoins DEV_HOME by itself: it was replaced,
            # not taken down, so its autoconnect isn't blocked.
            if not (dev and await self.in_io(net.active_profile) == DEV_HOME):
                await self.in_io(net.down)
        if not dev:
            with contextlib.suppress(Exception):
                await self.in_io(net.radio, False)
        self.share = "off"
        self.share_info = ShareInfo()
        self.last_use = self.clock()
        self.log("share: off")
        self.update_led()
        if restore:
            self._restore_display()

    def _restore_display(self) -> None:
        """Put back what the print showed before the share screen."""
        if self.lifecycle != ACTIVE or not self.module_present:
            return
        if self.state.undeveloped and self.store.get(self.state.undeveloped):
            self.queue_job(Job(DEVELOP, self.state.undeveloped))
        elif self.state.display_content == "share" or (
                self.display_job is not None and self.display_job.kind == SHARE_SCREEN):
            if self.state.current and self.store.get(self.state.current):
                self.queue_job(Job(SHOW, self.state.current, why="restore"))
            else:
                self.queue_job(Job(BLANK, why="restore"))

    async def share_switch(self) -> None:
        """Home Wi-Fi → the camera's own, for a phone that isn't on the home network, and back.
        If home Wi-Fi can't be found on the way back, sharing carries on over the camera's."""
        if self.share != "on":
            return
        to_hotspot = self.share_info.mode == "home"
        if not to_hotspot and not await self.in_io(self.hw.network.homes):
            return  # no home network saved
        self.log(f"share: switching to {'the camera' if to_hotspot else 'home'} Wi-Fi")
        await self.share_stop(restore=False)
        # If it fails, share_start puts the photo back: no QR code is left up that no longer works.
        await self.share_start(force_hotspot=to_hotspot)

    # Shutdown ------------------------------------------------------------------------------------------
    def request_shutdown(self, reason: str, reboot: bool = False) -> None:
        if self._shutdown_task is None:
            self._shutdown_task = self.spawn(self._shutdown(reason, reboot))

    async def _shutdown(self, reason: str, reboot: bool = False) -> None:
        if self.power_after_update and reason == "update":
            reason, reboot = "power_button", False
        self.log(f"shutdown: {reason}{' (reboot)' if reboot else ''}")
        previous = self.lifecycle
        self.lifecycle = SHUTTING_DOWN
        start = self.clock()
        # 2: let a running screen update finish (up to 30 s), drop the pending one.
        self.pending_job = None
        if self.display_job is not None:
            self.shutdown_display_busy = True
            self.update_led()
            if not await self.wait_display_idle(SHUTDOWN_DISPLAY_WAIT):
                self.display_worker.abort()
                await self.wait_display_idle(5)
        self.shutdown_display_busy = False
        self.update_led()
        # Stopped from outside: a reboot, the Pi turning off, or only this service restarting
        # (an install, a crash). Only the camera turning off may turn the screen white.
        service_only = False
        if self.external_stop:
            try:
                why = await self.in_io(self.hw.system.stopping_for)
            except Exception:
                why = "poweroff"
            reboot = reboot or why == "reboot"
            service_only = why is None
        # 3: the next wake decides whether the screen turns white.
        if reason == "low_battery":
            next_wake = None
        elif previous == WAKE and self.wake_armed:
            next_wake = self.state.next_wake_utc
        else:
            next_wake = slideshow.next_wake(time.time(), self.settings, self.battery_level, self.plugged,
                                            self.clock_valid, bool(self.store.list()))
        white = (next_wake is None and not reboot and not service_only and self.module_present
                 and self.hw.pins.module_present())
        side = [self.spawn(self.share_stop(restore=False)), self.spawn(self.capture.close())]
        if white:
            self.shutdown_display_busy = True
            self.update_led()
            self.display_job = Job(BLANK, why="shutdown")
            self.display = "refreshing"
            self.display_worker.submit(self.display_job)
            await self.wait_display_idle(60)
            self.shutdown_display_busy = False
            self.update_led()
        for t in side:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(t, 20)
        # 5: arm the wake, or disable the alarm.
        if not (previous == WAKE and self.wake_armed and reason == "slideshow"):
            if reason == "low_battery" or next_wake is None:
                if self.pisugar and self.pisugar.connected:
                    with contextlib.suppress(PiSugarError):
                        await self.pisugar.alarm_disable()
                self.state.next_wake_utc = None
            else:
                await self.arm_next_wake()
        # 6: save state.
        self.state.last_shutdown = {"reason": reason, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                    "uptime": round(self.hw.system.uptime(), 1)}
        self.save_state()
        self.save_slides()
        # 7: watchdog off (the shutdown hook turns it off again later).
        self._watchdog_off = True
        try:
            await self.in_io(self.hw.watchdog.disable)
        except Exception as e:
            self.log(f"watchdog: {e}")
        self.led.set_steady("off")
        self.uptime_mark(f"shutdown done ({reason}, {self.clock() - start:.1f} s)")
        # 8: power off or restart.
        if not self.external_stop:
            if reboot:
                await self.in_io(self.hw.system.reboot)
            else:
                await self.in_io(self.hw.system.poweroff)
        self._stopped.set()

    # Rebuilding screen images ------------------------------------------------------------------------------
    def _queue_rebuild(self, pid: str) -> None:
        if pid not in self.rebuild_queue:
            self.rebuild_queue.append(pid)
        self._kick_rebuild()

    def _kick_rebuild(self) -> None:
        if self.rebuild_queue and (self._rebuild_task is None or self._rebuild_task.done()) \
                and self.lifecycle == ACTIVE:
            self._rebuild_task = self.spawn(self._rebuild_loop())

    async def _rebuild_loop(self) -> None:
        while self.rebuild_queue and self.lifecycle == ACTIVE:
            if self.capture_busy:
                await asyncio.sleep(1)
                continue
            pid = self.rebuild_queue.pop(0)
            path = self.store.photo_path(pid)
            if not path.exists():
                continue

            def rebuild(pid=pid, path=path):
                from inkstant.imaging import pipeline

                epd, thumb = pipeline.from_jpeg(path)
                self.store.write_epd(pid, epd)
                self.store.write_print(pid, pipeline.print_png(epd))
                if not self.store.thumb_path(pid).exists():
                    self.store.write_thumb(pid, thumb)

            try:
                await self.capture.run(rebuild)
                self.log(f"rebuilt screen image for {pid}")
            except Exception as e:
                self.log(f"rebuild {pid}: {e!r}")
            await asyncio.sleep(0.2)

    def _stale_screen_images(self) -> list[str]:
        """Photos whose screen image was made with an older palette (or is unreadable)."""
        stale = []
        for p in self.store.list():
            try:
                with open(self.store.epd_path(p.id), "rb") as f:
                    head = f.read(epdfile.HEADER_BYTES)
                ok = len(head) == epdfile.HEADER_BYTES and head[:4] == epdfile.MAGIC and \
                    int.from_bytes(head[6:8], "little") == palette.PALETTE_VERSION
            except OSError:
                ok = False
            if not ok:
                stale.append(p.id)
        return stale

    async def queue_palette_rebuilds(self) -> int:
        """After a palette change, remake every screen image and print, oldest last."""
        n = 0
        for pid in await self.in_io(self._stale_screen_images):
            if pid not in self.rebuild_queue:
                self.rebuild_queue.append(pid)
                n += 1
        if n:
            self.log(f"palette changed: rebuilding {n} screen images")
        self._kick_rebuild()
        return n

    # Self-test ----------------------------------------------------------------------------------------------
    async def _first_start_selftest(self) -> None:
        raw, _ = atomic.read_json(self.paths.selftest)
        self.selftest_result = raw
        if raw and raw.get("version") == version.VERSION:
            return
        await asyncio.sleep(5)  # let the camera start first
        result = await self.run_selftest()
        if not result["passed"]:
            self.errors.set("setup", f"Setup check found a problem: {', '.join(result['failed'][:2])}")
            self.update_led()
            self.spawn(self._clear_setup_error_later())

    async def _clear_setup_error_later(self) -> None:
        await asyncio.sleep(30)
        self.errors.clear("setup")
        self.update_led()

    async def run_selftest(self) -> dict:
        loop = asyncio.get_running_loop()

        def pis_get(name: str) -> str:
            if not self.pisugar:
                raise OSError("no pisugar client")
            return asyncio.run_coroutine_threadsafe(self.pisugar.get(name), loop).result(5)

        def camera_present() -> bool:
            if self.camera_state in ("ready", "fallback", "asleep", "waking"):
                return True
            if self.capture.camera is not None and type(self.capture.camera).__name__ == "FakeCamera":
                return self.camera_state != "failed"
            try:
                from picamera2 import Picamera2

                return bool(Picamera2.global_camera_info())
            except Exception:
                return False

        def display_probe() -> bool | None:
            if not self.hw.pins.module_present():
                return None
            return True if self.display_job is not None else None  # the app owns the driver

        rtc = self.hw.system.rtc0_epoch() if self.hw.system.rtc0_is_pisugar() else None
        if rtc is None and self.clock_valid:
            rtc = int(time.time())
        checks = await self.in_io(lambda: selftest.run(
            self.paths, family=self.device.family, pisugar_get=pis_get, camera_present=camera_present,
            display_probe=display_probe, module_present=self.hw.pins.module_present, rtc_epoch=rtc))
        result = selftest.summary(checks)
        result["version"] = version.VERSION
        result["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.selftest_result = result
        with contextlib.suppress(OSError):
            await self.in_io(lambda: atomic.write_json(self.paths.selftest, result, mode=0o644, keep_backup=False,
                                                        tmp_dir=self.paths.tmp))
        self.log(f"selftest: {'passed' if result['passed'] else 'failed: ' + ', '.join(result['failed'])}")
        return result

    # Status ---------------------------------------------------------------------------------------------------
    def status(self) -> dict:
        try:
            free, total = self.store.disk()
        except OSError:
            free = total = 0
        photos = self.store.list()
        _, off_min, share_min = machine.effective_minutes(self.settings, self.saver)
        job = self.display_job
        developing = job is not None and job.kind in (DEVELOP, SHOW) and self.display == "refreshing"
        share = {"mode": None}
        if self.share in ("on", "starting", "stopping"):
            share = {"mode": self.share_info.mode, "url": self.share_info.url, "ssid": self.share_info.network,
                     "state": self.share, "timeout_min": share_min, "on_battery": not self.plugged}
        st = self.selftest_result or {}
        return {
            "lifecycle": self.lifecycle,
            "battery": {"level": None if self.battery_level is None else round(self.battery_level),
                        "plugged": self.plugged, "low": self.battery_low},
            "battery_saver": {"on": self.saver, "why": "manual" if self.settings["battery_saver"]["enabled"]
                              else ("auto" if self.state.saver_auto_active else None)},
            "storage": {"free_bytes": free, "total_bytes": total, "state": self.storage,
                        "photos_left": self.store.photos_left(free) if total else 0,
                        "used_bytes": sum(p.size for p in photos)},
            "clock_valid": self.clock_valid,
            "module_present": self.module_present,
            "camera": self.camera_state,
            "share": share,
            "errors": self.errors.payload(),
            "undeveloped": self.state.undeveloped,
            "current": self.state.current,
            "version": version.VERSION,
            "update": dict(self.update_status),
            "newest_seq": photos[0].seq if photos else 0,
            "photo_count": len(photos),
            "today": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tz": local_tz_name(),
            "display": {"state": "developing" if developing else "idle", "id": job.photo_id if developing else None},
            "wifi_5ghz": self.device.wifi_5ghz,
            "setup_check": {"passed": st.get("passed", True), "failed": st.get("failed", [])},
            "turn_off_min": off_min,
            "dev_mode": self.settings["dev_mode"],
            "model": self.hw.system.model(),
        }

    # Control socket --------------------------------------------------------------------------------------------
    async def handle_command(self, cmd: str, args: dict, role: str) -> Any:
        if cmd not in ("status", "share.activity", "selftest.get", "settings.get", "wifi.list", "update.report"):
            self.last_use = self.clock()
        h = getattr(self, "cmd_" + cmd.replace(".", "_"), None)
        if h is None:
            raise ipc.IpcError("unknown", f"Unknown command {cmd!r}.")
        try:
            result = h(**args)
        except TypeError as e:
            raise ipc.IpcError("invalid", f"Bad arguments: {e}") from None
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def cmd_status(self) -> dict:
        return self.status()

    async def cmd_photos_delete(self, ids: list | None = None, all: bool = False) -> dict:  # noqa: A002
        if all:
            ids = [p.id for p in self.store.list()]
        if not isinstance(ids, list) or not all and not ids:
            raise ipc.IpcError("invalid", "Choose some photos to delete.")
        deleted, missing = await self.in_io(self.store.delete, [str(i) for i in ids])
        changed = False
        for pid in deleted:
            if self.state.undeveloped == pid:
                self.state.undeveloped = None
                changed = True
            if self.state.current == pid:
                self.state.current = None
                changed = True
            if pid in self.rebuild_queue:
                self.rebuild_queue.remove(pid)
        if changed:
            self.save_state()
        self.refresh_storage()
        self.log(f"photos: deleted {len(deleted)}")
        return {"deleted": deleted, "missing": missing}

    def cmd_photos_show(self, id: str = "") -> dict:  # noqa: A002
        if not store.valid_id(id) or not self.store.get(id):
            raise ipc.IpcError("not_found", "That photo isn't on the camera any more.")
        if not self.module_present:
            raise ipc.IpcError("no_module", "No display module in the camera.")
        if self.lifecycle != ACTIVE:
            raise ipc.IpcError("not_ready", "The camera is busy starting or turning off.")
        where = self.queue_job(Job(SHOW, id, why="show_on_camera"))
        if where == "missing":
            raise ipc.IpcError("not_ready", "The screen image is being rebuilt; try again in a minute.")
        return {"queued": where}

    def cmd_settings_get(self) -> dict:
        return settings.public(self.settings)

    async def cmd_settings_set(self, **partial) -> dict:
        try:
            new = settings.merge(self.settings, partial)
        except settings.SettingsError as e:
            raise ipc.IpcError("invalid", f"{e.field}: {e.message}" if e.field else e.message) from None
        old = self.settings
        if partial.get("battery_saver", {}).get("enabled") is False and self.state.saver_auto_active:
            self.state.saver_auto_active = False
            self.state.saver_auto_dismissed = True
            self.save_state()
        if partial.get("battery_saver", {}).get("auto") is False and self.state.saver_auto_active:
            self.state.saver_auto_active = False
            self.save_state()
        await self.in_io(settings.save, self.paths.settings, new, self.paths.tmp)
        self.settings = new
        applied = []
        if new["hotspot"] != old["hotspot"]:
            with contextlib.suppress(Exception):
                await self.in_io(self.hw.network.set_hotspot, new["hotspot"]["ssid"], new["hotspot"]["password"])
            applied.append("hotspot")
        if new["charge_limit"] != old["charge_limit"]:
            await self._apply_charge_limit()
            applied.append("charge_limit")
        if new["dev_mode"] != old["dev_mode"]:
            await self._apply_dev_mode(new["dev_mode"])
            applied.append("dev_mode")
        await self.apply_saver()
        self.update_led()
        return {"settings": settings.public(new), "applied": applied}

    async def cmd_time_set(self, epoch_ms: int = 0, tz: str | None = None) -> None:
        if not isinstance(epoch_ms, (int, float)) or epoch_ms < version.BUILD_EPOCH * 1000:
            raise ipc.IpcError("invalid", "That time looks wrong.")
        if tz is not None and (not isinstance(tz, str) or len(tz) > 64):
            raise ipc.IpcError("invalid", "Bad time zone.")
        await self.set_time(int(epoch_ms), tz)

    async def cmd_wifi_list(self) -> dict:
        homes = await self.in_io(self.hw.network.homes)
        return {"home": [{"n": h.n, "ssid": h.ssid} for h in homes],
                "hotspot": self.settings["hotspot"]["ssid"], "max": 5, "wifi_5ghz": self.device.wifi_5ghz}

    async def cmd_wifi_home_set(self, ssid: str = "", password: str | None = None) -> dict:
        try:
            settings.validate_ssid(ssid, "ssid")
            if password:
                settings.validate_wpa_password(password, "password")
        except settings.SettingsError as e:
            raise ipc.IpcError("invalid", f"{e.field}: {e.message}") from None
        net = self.hw.network
        homes = await self.in_io(net.homes)
        n = next((h.n for h in homes if h.ssid == ssid), None) or await self.in_io(net.free_home_slot)
        if n is None:
            raise ipc.IpcError("full", "Forget a network to add another.")
        await self.in_io(net.set_home, n, ssid, password or None, self.settings["dev_mode"] and n == 1)
        return {"n": n}

    async def cmd_wifi_home_remove(self, n: int = 0) -> None:
        if not isinstance(n, int) or not 1 <= n <= 5:
            raise ipc.IpcError("invalid", "Bad network number.")
        await self.in_io(self.hw.network.remove_profile, f"inkstant-home-{n}")

    def cmd_share_stop(self) -> None:
        async def later():
            await asyncio.sleep(1.0)  # let the gallery answer the phone first
            await self.share_stop()

        self.spawn(later())

    def cmd_share_activity(self) -> None:
        self.share_activity = self.clock()

    async def cmd_share_start(self, force_hotspot: bool = False) -> dict:
        # Shielded: if the request gives up waiting, sharing still finishes starting (or tears down)
        # instead of being cancelled halfway and left "starting".
        if self.lifecycle != ACTIVE:
            raise ipc.IpcError("not_ready", "The camera is busy starting or turning off.")
        if self.share in ("starting", "stopping"):
            raise ipc.IpcError("busy", f"Sharing is {self.share}; try again in a moment.")
        why = await asyncio.shield(self.spawn(self.share_start(force_hotspot=bool(force_hotspot))))
        if self.share != "on":
            raise ipc.IpcError("failed", f"Couldn't start sharing: {why or 'it was stopped'}")
        return {"mode": self.share_info.mode, "url": self.share_info.url, "ssid": self.share_info.network}

    async def cmd_update_check(self) -> dict:
        from inkstant.camera_app import update

        if self.share_info.mode != "home":
            raise ipc.IpcError("offline", "Needs internet. Share at home to update.")
        try:
            latest = await self.in_io(update.check_latest)
        except update.NoRelease:
            return {"current": version.VERSION, "latest": None, "available": False, "notes": ""}
        except Exception as e:
            raise ipc.IpcError("unavailable", f"Couldn't check for updates: {e}") from None
        return {"current": version.VERSION, "latest": latest["version"],
                "available": update.newer(latest["version"], version.VERSION), "notes": latest["notes"]}

    async def cmd_update_start(self, **args) -> dict:
        from inkstant.camera_app import update

        target = args.get("version")
        if self.share_info.mode != "home":
            raise ipc.IpcError("offline", "Needs internet. Share at home to update.")
        if not self.plugged and (self.battery_level is None or self.battery_level < 30):
            raise ipc.IpcError("battery", "Charge the camera, or wait until it's above 30%.")
        if self.update_running:
            raise ipc.IpcError("busy", "An update is already running.")
        if not isinstance(target, str) or not target:
            raise ipc.IpcError("invalid", "Which version?")
        cmd = update.start_command(target, str(self.paths.opt / "current"))
        res = await self.in_io(lambda: __import__("subprocess").run(cmd, capture_output=True, text=True, timeout=20))
        if res.returncode != 0:
            raise ipc.IpcError("failed", f"Couldn't start the update: {res.stderr.strip()[:200]}")
        self.update_running = True
        self.update_status = {"state": "running", "step": "downloading", "message": None}
        self.update_led()
        return {"started": target}

    def cmd_update_report(self, state: str = "running", step: str | None = None, message: str | None = None) -> None:
        self.update_status = {"state": state, "step": step, "message": message}
        if state == "running":
            self.update_running = True
        elif state in ("failed", "done"):
            self.update_running = state == "done"
            if state == "failed":
                self.log(f"update failed: {message}")
                self.last_use = self.clock()
                if self.power_after_update:
                    self.power_after_update = False
                    self.request_shutdown("power_button")
        self.update_led()

    def cmd_selftest_get(self) -> dict:
        return self.selftest_result or {"passed": True, "failed": [], "checks": []}

    async def cmd_selftest_run(self) -> dict:
        return await self.run_selftest()

    def cmd_power_button(self) -> dict:
        return {"action": self.power_button()}

    def cmd_shutdown(self, reason: str = "system", reboot: bool = False) -> dict:
        if reboot and self.power_after_update:
            self.request_shutdown("power_button")
        else:
            self.request_shutdown("update" if reboot else reason, reboot=bool(reboot))
        return {"started": True}

    async def cmd_dev_set(self, enabled: bool = False) -> dict:
        return await self.cmd_settings_set(dev_mode=bool(enabled))

    async def _apply_dev_mode(self, on: bool) -> None:
        net = self.hw.network
        with contextlib.suppress(Exception):
            await self.in_io(net.set_home_autoconnect, 1, on)
            if on:
                await self.in_io(net.radio, True)
            elif self.share == "off":
                await self.in_io(net.radio, False)

    def cmd_regen_epd(self) -> dict:
        for p in self.store.list():
            if p.id not in self.rebuild_queue:
                self.rebuild_queue.append(p.id)
        self._kick_rebuild()
        return {"queued": len(self.rebuild_queue)}


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
