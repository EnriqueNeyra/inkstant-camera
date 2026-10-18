"""The PiSugar 3's hardware watchdog and BTN taps, through smbus2.

Registers (I2C 0x57): 0x06 bit 7 = enable, bit 5 = feed; 0x07 = timeout in 2 s units;
0x08 bits 0-1 = the last BTN tap (1 single, 2 double, 3 long; cleared by the reader);
0x0B = write protection (0x29 unlocks, 0x00 locks). pisugar-server unlocks and relocks
0x0B around its own writes, so every protected write is read back and retried.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

ADDR = 0x57
REG_WATCHDOG = 0x06
REG_TIMEOUT = 0x07
REG_PROTECT = 0x0B
UNLOCK, LOCK = 0x29, 0x00
BIT_ENABLE = 0x80
BIT_FEED = 0x20
TIMEOUT_UNITS = 30  # × 2 s = 60 s
TRIES = 5
REG_TAP = 0x08
TAP_BITS = 0b11
TAPS = {1: "single", 2: "double", 3: "long"}
TAP_POLL = 0.02  # s
TAP_SLOW_POLL = 1.0  # s, once the PiSugar has stopped answering
TAP_ERRORS_BEFORE_SLOW = 10


class WatchdogError(Exception):
    pass


class Watchdog:
    def __init__(self, bus=None, log=None, sleep=time.sleep):
        self._bus = bus
        self.log = log or (lambda *_: None)
        self.sleep = sleep

    @property
    def bus(self):
        if self._bus is None:
            from smbus2 import SMBus

            self._bus = SMBus(1)
        return self._bus

    def _r(self, reg: int) -> int:
        return self.bus.read_byte_data(ADDR, reg)

    def _w(self, reg: int, value: int) -> None:
        self.bus.write_byte_data(ADDR, reg, value & 0xFF)

    def enable(self) -> None:
        for attempt in range(1, TRIES + 1):
            try:
                self._w(REG_PROTECT, UNLOCK)
                self._w(REG_TIMEOUT, TIMEOUT_UNITS)
                self._w(REG_WATCHDOG, self._r(REG_WATCHDOG) | BIT_ENABLE)
                self._w(REG_PROTECT, LOCK)
                if self._r(REG_TIMEOUT) == TIMEOUT_UNITS and self._r(REG_WATCHDOG) & BIT_ENABLE:
                    return
            except OSError as e:
                self.log(f"watchdog: enable attempt {attempt}: {e}")
            self.sleep(0.05)
        raise WatchdogError("couldn't turn the PiSugar watchdog on")

    def feed(self) -> None:
        # Not write-protected.
        self._w(REG_WATCHDOG, self._r(REG_WATCHDOG) | BIT_FEED)

    def disable(self) -> None:
        for attempt in range(1, TRIES + 1):
            try:
                self._w(REG_PROTECT, UNLOCK)
                self._w(REG_WATCHDOG, self._r(REG_WATCHDOG) & ~BIT_ENABLE)
                self._w(REG_PROTECT, LOCK)
                if not self._r(REG_WATCHDOG) & BIT_ENABLE:
                    return
            except OSError as e:
                self.log(f"watchdog: disable attempt {attempt}: {e}")
            self.sleep(0.05)
        raise WatchdogError("couldn't turn the PiSugar watchdog off")


class TapReader:
    """BTN taps read straight from 0x08, every 20 ms.

    pisugar-server reads 0x08 too, but only every 500 ms, which is too slow for a shutter.
    Whichever reader gets to a tap first clears it, so most taps arrive here and the rest
    through pisugar-server's socket. Reading and clearing can't be done in one step, so now
    and then both get the same tap; the app drops the second copy."""

    def __init__(self, bus=None, log=None, interval: float = TAP_POLL):
        self._bus = bus
        self.log = log or (lambda *_: None)
        self.interval = interval

    @property
    def bus(self):
        if self._bus is None:
            from smbus2 import SMBus

            self._bus = SMBus(1)
        return self._bus

    def read(self) -> str | None:
        """The tap waiting in 0x08, or None. A tap only counts once it's cleared (the same
        write pisugar-server makes, not write-protected), so a failed write can't report it twice."""
        value = self.bus.read_byte_data(ADDR, REG_TAP)
        tap = TAPS.get(value & TAP_BITS)
        if tap:
            self.bus.write_byte_data(ADDR, REG_TAP, value & ~TAP_BITS & 0xFF)
        return tap

    def run(self, on_tap: Callable[[str], None], stop: threading.Event) -> None:
        """Poll until `stop` is set; runs in a thread of its own. The PiSugar 3 fails a read now
        and then, so it only slows down after several failures in a row."""
        errors = 0
        while not stop.wait(TAP_SLOW_POLL if errors >= TAP_ERRORS_BEFORE_SLOW else self.interval):
            try:
                tap = self.read()
            except OSError as e:
                errors += 1
                if errors == TAP_ERRORS_BEFORE_SLOW:
                    self.log(f"btn: reading taps keeps failing ({e}); trying once a second")
                continue
            if errors >= TAP_ERRORS_BEFORE_SLOW:
                self.log("btn: reading taps again")
            errors = 0
            if tap:
                on_tap(tap)


class FakeBus:
    """Registers in memory. `relock_at` lists write numbers (1-based) just before which
    pisugar-server relocks 0x0B, as it does now and then around its own writes."""

    def __init__(self, relock_at: set[int] | None = None):
        self.regs = {REG_WATCHDOG: 0x00, REG_TIMEOUT: 0x00, REG_PROTECT: LOCK, REG_TAP: 0x00}
        self.relock_at = relock_at or set()
        self._writes = 0
        self.feeds = 0

    def read_byte_data(self, addr: int, reg: int) -> int:
        assert addr == ADDR
        return self.regs.get(reg, 0)

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        assert addr == ADDR
        self._writes += 1
        if self._writes in self.relock_at:
            self.regs[REG_PROTECT] = LOCK  # pisugar-server got in between
        if reg in (REG_PROTECT, REG_TAP):
            self.regs[reg] = value
            return
        if reg == REG_WATCHDOG and value & BIT_FEED:
            self.feeds += 1
            value &= ~BIT_FEED  # the feed bit self-clears
            # Feeding isn't protected; the enable bit is.
            if self.regs[REG_PROTECT] != UNLOCK:
                value = (value & ~BIT_ENABLE) | (self.regs[reg] & BIT_ENABLE)
            self.regs[reg] = value
            return
        if self.regs[REG_PROTECT] != UNLOCK:
            return  # silently ignored, like the real write protection
        self.regs[reg] = value

    def tap(self, name: str) -> None:
        """BTN, as the firmware reports it."""
        code = {v: k for k, v in TAPS.items()}[name]
        self.regs[REG_TAP] = (self.regs[REG_TAP] & ~TAP_BITS) | code
