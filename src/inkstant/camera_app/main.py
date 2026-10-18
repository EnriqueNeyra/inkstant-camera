"""Start the camera app on the real hardware (inkstant.service)."""

from __future__ import annotations

import asyncio
import sys

from inkstant import device
from inkstant.camera_app.app import CameraApp, Hardware
from inkstant.paths import Paths


def log(msg: str) -> None:
    print(msg, flush=True)  # journald timestamps each line


def build_hardware(paths: Paths) -> Hardware:
    from inkstant.hw.camera import Camera
    from inkstant.hw.epd import EpdDriver
    from inkstant.hw.gpio import Pins
    from inkstant.hw.led import SysfsLed
    from inkstant.hw.network import Network
    from inkstant.hw.pisugar_i2c import TapReader, Watchdog
    from inkstant.hw.system import System

    dev = device.load(paths.device_toml)
    system = System(log=log)
    model = system.model()
    pins = Pins()
    return Hardware(
        pins=pins,
        led_writer=SysfsLed(paths.leds),
        epd=EpdDriver(pins, log=log),
        camera_factory=lambda: Camera(dev, log=log),
        network=Network(log=log),
        system=system,
        watchdog=Watchdog(log=log),
        taps=TapReader(log=log),
        is_3b="Raspberry Pi 3 Model B" in model,  # the 3B and 3B+ have the USB hub
    )


def main(argv: list[str]) -> int:
    paths = Paths.default()
    try:
        hw = build_hardware(paths)
    except Exception as e:
        log(f"camera app: can't open the hardware: {e!r}")
        return 1
    app = CameraApp(paths, hw, log=log)
    try:
        asyncio.run(app.run())
    finally:
        try:
            hw.pins.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
