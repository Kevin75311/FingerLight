"""LED outputs for the app: the real strip over SPI, or nothing.

The bar itself is drawn by the browser preview from the engine's state, so a PC needs no
sink at all.  On the Pi, `SpiSink` reuses the bench-tested encoder in
`tools/hw/ws2812_spi.py` -- the same bytes `led_test.py` proved on the strip, rather
than a second implementation of the same timing.
"""

from __future__ import annotations

import importlib.util
import os

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DRIVER = os.path.join(_REPO, "tools", "hw", "ws2812_spi.py")


def _load_driver():
    spec = importlib.util.spec_from_file_location("ws2812_spi", _DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SpiSink:
    """176 WS2812s on SPI0 / GPIO10 (D-28).  `dry=True` counts frames without spidev."""

    def __init__(self, n: int, order: str = "GRB", one_pattern: str = "1110",
                 dry: bool = False):
        drv = _load_driver()
        self.n = n
        self.dry = dry
        self.strip = drv.Strip(n, order=order, one=int(one_pattern, 2)).open(dry=dry)
        self.frames = 0

    def describe(self) -> str:
        return "spi-dry" if self.dry else "spi0 (GPIO10)"

    def show(self, pixels: dict[int, tuple[int, int, int]], brightness: float) -> None:
        self.strip.clear()
        for i, rgb in pixels.items():
            if 0 <= i < self.n:
                self.strip[i] = rgb
        self.strip.brightness = brightness
        self.strip.show()
        self.frames += 1

    def close(self) -> None:
        try:
            self.strip.clear()
            self.strip.show()
        finally:
            self.strip.close()


class NullSink:
    """No strip attached: the browser preview is the bar."""

    def describe(self) -> str:
        return "preview only"

    def show(self, pixels, brightness) -> None:
        pass

    def close(self) -> None:
        pass


def spi_present() -> bool:
    return os.path.exists("/dev/spidev0.0") and importlib.util.find_spec("spidev") is not None


def make_sink(kind: str, n: int, order: str, one_pattern: str):
    """`kind`: "auto" (SPI when the Pi has it), "spi", "dry" or "none"."""
    if kind == "auto":
        kind = "spi" if spi_present() else "none"
    if kind == "none":
        return NullSink()
    return SpiSink(n, order=order, one_pattern=one_pattern, dry=(kind == "dry"))
