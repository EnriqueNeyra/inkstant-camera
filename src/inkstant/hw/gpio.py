"""GPIO through gpiozero with the lgpio pin factory.

The SPI driver owns GPIO 7 to 11 and I2C owns 2 and 3, so none of those are opened here.
GPIO 0 (ID_SD, pin 27) is only read as an input: nothing uses the ID chip's I2C bus.
Tests and the simulator use gpiozero's MockFactory.
"""

from __future__ import annotations

DETECT = 0  # ID_SD, pin 27
BUSY = 24
RST = 17
DC = 25
PWR = 18


def mock_factory():
    from gpiozero.pins.mock import MockFactory

    return MockFactory()


class Pins:
    def __init__(self, factory=None):
        from gpiozero import DigitalInputDevice, DigitalOutputDevice

        kw = {"pin_factory": factory} if factory is not None else {}
        self.factory = factory
        # Print detect: the print (the e-paper HAT, on the header) has its own pull-up on ID_SD for its
        # ID chip. Pulled down here, so "active" (high) means a print is on.
        self.detect = DigitalInputDevice(DETECT, pull_up=False, bounce_time=0.05, **kw)
        # BUSY: pulled down; high means the panel is idle, low means busy or no module.
        self.busy = DigitalInputDevice(BUSY, pull_up=False, **kw)
        self.rst = DigitalOutputDevice(RST, initial_value=False, **kw)
        self.dc = DigitalOutputDevice(DC, initial_value=False, **kw)
        self.pwr = DigitalOutputDevice(PWR, initial_value=False, **kw)

    def module_present(self) -> bool:
        return bool(self.detect.is_active)

    def close(self) -> None:
        for dev in (self.detect, self.busy, self.rst, self.dc, self.pwr):
            try:
                dev.close()
            except Exception:
                pass
