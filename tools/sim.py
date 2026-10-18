#!/usr/bin/env python3
"""Desktop simulator: the real camera app with every fake, plus the gallery.

    python3 tools/sim.py [--root sim] [--port 8080] [--no-module]

Commands (type then Enter, or write lines to <root>/ctl, a FIFO):
    b  BTN tap (photo)  h  BTN hold (sharing on / off)
    d  BTN double tap (a photo; while sharing: home Wi-Fi <-> the camera's own)
    p  PWR (on / off)   w  power on as a slideshow (timed) wake
    m  print on / off   c  plug the charger in/out
    l  battery level, e.g. "l 15"      q  quit
The display is written to <root>/display.png; the light's pattern is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from inkstant import settings, state  # noqa: E402
from inkstant.camera_app.app import CameraApp, Hardware  # noqa: E402
from inkstant.hw import camera, epd, gpio, led, network, pisugar, pisugar_i2c, system  # noqa: E402
from inkstant.paths import Paths  # noqa: E402


class PrintingLed(led.FakeLed):
    def __init__(self):
        super().__init__()
        self.last = None

    def _say(self, what):
        if what != self.last:
            self.last = what
            print(f"  [light] {what}", flush=True)

    def set_level(self, value):
        super().set_level(value)
        self._say("on" if value else "off")

    def set_timer(self):
        super().set_timer()
        self._say("slow blink (starting)")

    def set_pattern(self, pattern, repeat):
        super().set_pattern(pattern, repeat)
        names = {v[1]: k for k, v in led.STEADY.items() if v[0] == "pattern"}
        self._say(names.get(pattern, pattern))


class Sim:
    def __init__(self, root: Path, port: int, module: bool):
        self.paths = Paths(root.resolve())
        self.paths.ensure_dirs()
        self.port = port
        self.factory = gpio.mock_factory()
        self.pins = gpio.Pins(self.factory)
        if module:
            self.factory.pin(gpio.DETECT).drive_high()
        self.app: CameraApp | None = None
        self.app_task: asyncio.Task | None = None
        self.server = pisugar.FakePiSugarServer(self.paths.pisugar_sock)
        # The setup check needs a real Pi; mark it done so the simulator's light isn't stuck on "problem".
        import json

        from inkstant import version
        self.paths.selftest.write_text(json.dumps({"version": version.VERSION, "passed": True, "failed": [],
                                                   "checks": [{"name": "Simulator", "ok": True, "critical": False,
                                                               "detail": "no hardware checks", "hint": ""}]}))
        if not self.paths.settings.exists():
            s = settings.validate(settings.DEFAULTS)
            s["hotspot"] = {"ssid": "Inkstant-SIM", "password": "simulator1"}
            settings.save(self.paths.settings, s)

    def log(self, msg: str) -> None:
        print(f"  {msg}", flush=True)

    def hardware(self) -> Hardware:
        led_w = PrintingLed()
        return Hardware(
            pins=self.pins, led_writer=led_w,
            epd=epd.FakeEpd(self.paths.root / "display.png", self.pins.module_present, speed=0.15, log=self.log),
            camera_factory=lambda: camera.FakeCamera(delay=0.2, log=self.log),
            network=network.FakeNetwork(url=f"http://127.0.0.1:{self.port}/"),
            system=self.system, watchdog=pisugar_i2c.Watchdog(pisugar_i2c.FakeBus(), sleep=lambda s: None),
            gallery_health_url=f"http://127.0.0.1:{self.port}/healthz",
            start_gallery=lambda: None, stop_gallery=lambda: None, trust_all_callers=True)

    async def power_on(self, timed: bool = False) -> None:
        if self.app_task and not self.app_task.done():
            return
        if timed:
            st = state.load_state(self.paths.state)
            st.next_wake_utc = time.time() - 4
            state.save_state(self.paths.state, st)
        self.system = system.FakeSystem()
        self.app = CameraApp(self.paths, self.hardware(), log=self.log)
        print("== power on" + (" (slideshow wake)" if timed else ""), flush=True)
        self.app_task = asyncio.create_task(self.app.run())
        self.app_task.add_done_callback(lambda t: print("== camera off (p: power on, w: timed wake)", flush=True))

    def start_gallery(self) -> None:
        from waitress import create_server

        from inkstant.gallery.app import create_app

        app = create_app(self.paths, control_sock=self.paths.control_sock, hostname="inkstant")
        srv = create_server(app, host="127.0.0.1", port=self.port, threads=4)
        threading.Thread(target=srv.run, daemon=True).start()
        print(f"gallery: http://127.0.0.1:{self.port}/   display: {self.paths.root / 'display.png'}", flush=True)

    async def command(self, line: str) -> bool:
        parts = line.strip().split()
        if not parts:
            return True
        c = parts[0]
        if c == "q":
            return False
        if c in ("h", "b", "d"):
            await self.server.tap({"h": "long", "b": "single", "d": "double"}[c])
        elif c == "p":
            if self.app_task and not self.app_task.done():
                r = await self.app.handle_command("power.button", {}, "root")
                print(f"  PWR → {r['action']}", flush=True)
            else:
                await self.power_on()
        elif c == "w":
            await self.power_on(timed=True)
        elif c == "m":
            pin = self.factory.pin(gpio.DETECT)
            if pin.state:
                pin.drive_low()
                print("  module out", flush=True)
            else:
                pin.drive_high()
                print("  module in", flush=True)
        elif c == "c":
            v = self.server.values
            v["battery_power_plugged"] = "false" if v["battery_power_plugged"] == "true" else "true"
            print(f"  charger {'in' if v['battery_power_plugged'] == 'true' else 'out'}", flush=True)
        elif c == "l" and len(parts) > 1:
            self.server.values["battery"] = parts[1]
            if self.app:
                await self.app.read_battery()
        else:
            print(__doc__, flush=True)
        return True

    async def run(self) -> None:
        await self.server.start()
        self.start_gallery()
        await self.power_on()
        fifo = self.paths.root / "ctl"
        with contextlib.suppress(FileExistsError):
            os.mkfifo(fifo)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()

        def read_fifo():
            while True:
                with open(fifo) as f:
                    for line in f:
                        loop.call_soon_threadsafe(queue.put_nowait, line)

        def read_stdin():
            for line in sys.stdin:
                loop.call_soon_threadsafe(queue.put_nowait, line)
            loop.call_soon_threadsafe(queue.put_nowait, "")

        threading.Thread(target=read_fifo, daemon=True).start()
        if sys.stdin.isatty():
            threading.Thread(target=read_stdin, daemon=True).start()
        while True:
            line = await queue.get()
            if not await self.command(line):
                break
        if self.app_task and not self.app_task.done():
            self.app.request_shutdown("system")
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.app_task, 30)
        await self.server.stop()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "sim"))
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--no-module", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("GPIOZERO_PIN_FACTORY", "mock")
    try:
        asyncio.run(Sim(Path(a.root), a.port, not a.no_module).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
