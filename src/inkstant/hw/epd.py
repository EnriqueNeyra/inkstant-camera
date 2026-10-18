"""E-paper driver: our port of Waveshare's epd3in6e.

Same commands, data and delays as Waveshare's driver, plus BUSY timeouts, abort, PWR held
low when idle, and sending a ready-made frame. Runs in the display worker thread.

The command sequence comes from Waveshare's epd3in6e.py (https://github.com/waveshareteam/e-Paper),
which carries this notice:

    Permission is hereby granted, free of charge, to any person obtaining a copy of this software
    and associated documentation files (the "Software"), to deal in the Software without
    restriction, including without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all copies or
    substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
    INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
    PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR
    ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
    ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from inkstant.imaging import epdfile


class EpdError(Exception):
    pass


class ModuleMissing(EpdError):
    """BUSY never went high after reset: no module, or an unpowered one."""


class DisplayTimeout(EpdError):
    """BUSY stayed low too long with a module apparently present."""


class Aborted(EpdError):
    """The module was pulled out, or shutdown couldn't wait any longer."""


INIT_SEQUENCE = (
    (0xAA, (0x49, 0x55, 0x20, 0x08, 0x09, 0x18)),
    (0x01, (0x3F,)),
    (0x00, (0x5F, 0x69)),
    (0x05, (0x40, 0x1F, 0x1F, 0x2C)),
    (0x08, (0x6F, 0x1F, 0x1F, 0x22)),
    (0x06, (0x6F, 0x1F, 0x17, 0x17)),
    (0x03, (0x00, 0x54, 0x00, 0x44)),
    (0x60, (0x02, 0x00)),
    (0x30, (0x08,)),
    (0x50, (0x3F,)),
    (0x61, (0x01, 0x90, 0x02, 0x58)),  # 400 × 600
    (0xE3, (0x2F,)),
    (0x84, (0x01,)),
)
BUSY_SETTLE = 0.2  # Waveshare adds 200 ms after every BUSY release
TIMEOUT_RESET = 2.0
TIMEOUT_STEP = 5.0
TIMEOUT_REFRESH = 45.0
SLEEP_DELAY = 2.0  # Waveshare's delay after deep sleep, before switching PWR off


class Spi:
    """spidev on /dev/spidev0.0: hardware CE0, mode 0, 4 MHz, as Waveshare's driver."""

    def __init__(self):
        self.dev = None

    def open(self) -> None:
        import spidev

        self.dev = spidev.SpiDev()
        self.dev.open(0, 0)
        self.dev.max_speed_hz = 4_000_000
        self.dev.mode = 0

    def write(self, data: list[int]) -> None:
        self.dev.writebytes(data)

    def write_bulk(self, data: bytes) -> None:
        # writebytes2 splits into transfers of spidev's buffer size (4096 bytes by default).
        self.dev.writebytes2(data)

    def close(self) -> None:
        if self.dev is not None:
            try:
                self.dev.close()
            finally:
                self.dev = None


class EpdDriver:
    def __init__(self, pins, spi: Spi | None = None, log=None, sleep: Callable[[float], None] | None = None):
        self.pins = pins
        self.spi = spi or Spi()
        self.log = log or (lambda *_: None)
        self._wake = threading.Event()
        self._abort = threading.Event()
        self._reset_seen = threading.Event()  # BUSY went low after the last reset
        self._sleep = sleep
        pins.busy.when_activated = self._wake.set
        pins.busy.when_deactivated = self._reset_seen.set

    # Abort -------------------------------------------------------------------
    def request_abort(self) -> None:
        self._abort.set()
        self._wake.set()

    def _check(self) -> None:
        if self._abort.is_set():
            raise Aborted()

    def _delay(self, seconds: float) -> None:
        if self._sleep is not None:
            self._sleep(seconds)
            self._check()
            return
        if self._abort.wait(seconds):
            raise Aborted()

    def _wait_busy(self, timeout: float, exc: type[EpdError]) -> None:
        """Wait for BUSY high (idle), then Waveshare's 200 ms. No polling: the edge wakes us."""
        deadline = time.monotonic() + timeout
        while True:
            self._check()
            self._wake.clear()
            if self.pins.busy.value:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise exc(f"BUSY stayed low for {timeout:.0f} s")
            self._wake.wait(min(remaining, 1.0))
        self._delay(BUSY_SETTLE)

    # Bus -----------------------------------------------------------------------
    def _command(self, cmd: int) -> None:
        self.pins.dc.off()
        self.spi.write([cmd])

    def _data(self, values) -> None:
        self.pins.dc.on()
        for v in values:  # one byte per transfer, as Waveshare's send_data
            self.spi.write([v])

    def _power_on(self) -> None:
        self.pins.pwr.on()
        self.spi.open()

    def _off(self) -> None:
        try:
            self.spi.close()
        finally:
            self.pins.rst.off()
            self.pins.dc.off()
            self.pins.pwr.off()

    def _reset_and_wait(self) -> None:
        self.pins.rst.on()
        self._delay(0.020)
        self.pins.rst.off()
        self._delay(0.002)
        self._reset_seen.clear()
        self.pins.rst.on()
        self._delay(0.020)
        self._wait_busy(TIMEOUT_RESET, ModuleMissing)

    # Public ---------------------------------------------------------------------
    def show(self, frame: bytes, on_shown: Callable[[], None] | None = None) -> None:
        """Send a 120,000-byte frame and refresh. Calls `on_shown` when the image is shown (end
        of step 8); the panel's power-down tail (about 2.4 s) runs after that."""
        if len(frame) != epdfile.FRAME_BYTES:
            raise ValueError("bad frame size")
        self._abort.clear()
        self._wake.clear()
        try:
            self._power_on()  # 1
            self._reset_and_wait()  # 2
            self._delay(0.030)  # 3
            for cmd, data in INIT_SEQUENCE:  # 4
                self._check()
                self._command(cmd)
                self._data(data)
            self._wait_busy(TIMEOUT_STEP, DisplayTimeout)
            self._command(0x10)  # 5
            self.pins.dc.on()
            self.spi.write_bulk(frame)
            self._check()
            self._command(0x04)  # 6 power on
            self._wait_busy(TIMEOUT_STEP, DisplayTimeout)
            self._command(0x06)  # 7 booster
            self._data((0x6F, 0x1F, 0x17, 0x27))
            self._delay(0.200)
            self._command(0x12)  # 8 refresh
            self._data((0x00,))
            self._wait_busy(TIMEOUT_REFRESH, DisplayTimeout)
            if on_shown:
                on_shown()
            self._command(0x02)  # 9 power off
            self._data((0x00,))
            self._wait_busy(TIMEOUT_STEP, DisplayTimeout)
            self._command(0x07)  # 10 deep sleep
            self._data((0xA5,))
            self._delay(SLEEP_DELAY)
        finally:
            self._off()  # 11

    def probe(self) -> bool:
        """Self-test: power, reset and wait for BUSY. No command or data is sent, so nothing is
        refreshed and the panel keeps its picture. True if a module answers; aborts as show() does.

        A module answers when BUSY drops as the reset ends (about 7 ms) and comes back high. BUSY
        reading high alone proves little: a print on the header reads high even with PWR low."""
        self._abort.clear()
        try:
            self._power_on()
            self._reset_and_wait()
            return self._reset_seen.is_set()
        except ModuleMissing:
            return False
        finally:
            self._off()

    def close(self) -> None:
        self.pins.busy.when_activated = None
        self.pins.busy.when_deactivated = None


class FakeEpd:
    """The simulator's display: writes each shown frame as a PNG, with shortened timings."""

    def __init__(self, png_path: Path, module_present: Callable[[], bool], speed: float = 0.2,
                 rotation: int = 0, log=None):
        self.png_path = Path(png_path)
        self.module_present = module_present
        self.speed = speed
        self.rotation = rotation
        self.log = log or (lambda *_: None)
        self._abort = threading.Event()
        self.shown: list[bytes] = []

    def request_abort(self) -> None:
        self._abort.set()

    def _delay(self, seconds: float) -> None:
        if self._abort.wait(seconds * self.speed):
            raise Aborted()

    def show(self, frame: bytes, on_shown=None) -> None:
        self._abort.clear()
        if not self.module_present():
            self._delay(TIMEOUT_RESET)
            raise ModuleMissing("no module")
        self._delay(3.0)
        self._delay(14.0)  # the refresh
        self.shown.append(frame)
        self._write_png(frame)
        if on_shown:
            on_shown()
        self._delay(2.4)

    def _write_png(self, frame: bytes) -> None:
        try:
            from inkstant.imaging import pipeline

            self.png_path.parent.mkdir(parents=True, exist_ok=True)
            # The frame arrives oriented for the mounting; undo that to picture what the user sees.
            img = pipeline.frame_to_image(epdfile.oriented(frame, self.rotation))
            tmp = self.png_path.with_suffix(".tmp.png")
            img.save(tmp)
            tmp.replace(self.png_path)
        except Exception as e:  # the simulator's picture is a nicety
            self.log(f"fake epd: {e}")

    def probe(self) -> bool:
        self._abort.clear()
        if not self.module_present():
            self._delay(TIMEOUT_RESET)
            return False
        self._delay(0.25)  # the reset pulse, BUSY and Waveshare's 200 ms
        return True

    def close(self) -> None:
        pass
