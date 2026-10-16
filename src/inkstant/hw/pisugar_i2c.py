"""The PiSugar 3's hardware watchdog, through smbus2 (SPEC.md §6.6).

Registers (I2C 0x57): 0x06 bit 7 = enable, bit 5 = feed; 0x07 = timeout in 2 s units;
0x0B = write protection (0x29 unlocks, 0x00 locks). pisugar-server unlocks and relocks
0x0B around its own writes, so every protected write is read back and retried.
"""

from __future__ import annotations

import time

ADDR = 0x57
REG_WATCHDOG = 0x06
REG_TIMEOUT = 0x07
REG_PROTECT = 0x0B
UNLOCK, LOCK = 0x29, 0x00
BIT_ENABLE = 0x80
BIT_FEED = 0x20
TIMEOUT_UNITS = 30  # × 2 s = 60 s
TRIES = 5


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


class FakeBus:
    """Registers in memory. `relock_at` lists write numbers (1-based) just before which
    pisugar-server relocks 0x0B, as it does now and then around its own writes."""

    def __init__(self, relock_at: set[int] | None = None):
        self.regs = {REG_WATCHDOG: 0x00, REG_TIMEOUT: 0x00, REG_PROTECT: LOCK}
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
        if reg == REG_PROTECT:
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
