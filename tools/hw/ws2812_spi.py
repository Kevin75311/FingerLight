"""Drive WS2812B/SK6812 from a Raspberry Pi 5 over SPI (D-28).

Why this file exists: `rpi_ws281x` DMAs into BCM2835 PWM/PCM peripherals and does
not work on a Pi 5, whose GPIO sits behind RP1. SPI does work, and a WS2812 bit
can be spelled with four SPI bits:

    SPI clock 3.2 MHz  ->  one SPI bit = 312.5 ns
    WS2812 bit period  =  1.25 us      = four SPI bits
        logical 0 -> 1000   (T0H = 312 ns, datasheet wants 200-500 ns)
        logical 1 -> 1110   (T1H = 937 ns, datasheet wants 550-850 ns... see below)

`1110` is 937 ns, slightly over the datasheet's nominal T1H window, and it is what
almost every 4-bit encoder in the wild uses because WS2812B parts accept it. If a
strip misbehaves, `--one-pattern 1100` drops T1H to 625 ns, which is dead centre.
That is a one-flag experiment on real hardware, which is the point of exposing it.

Frame size for this bar: 176 LEDs x 24 bits x 4 SPI bits / 8 = **2112 bytes** of
pixels, plus 120 low bytes before and 120 after (300 us each, see `RESET_US`) = 2352,
comfortably inside spidev's stock 4096-byte limit. (Libraries that spell a bit
with eight SPI bits need 4224 bytes and therefore a bigger `spidev.bufsiz` --
worth knowing if you swap this out for `neopixel_spi`.)

No Pi is needed to check the encoder: `python tools/hw/ws2812_spi.py --selftest`
runs it against hand-derived expectations.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

#: Low time on the wire before *and* after every frame.  The original WS2812 latches
#: after 50 us of low, but the WS2812B-V5 and most current clones need **280 us**: with
#: less, two frames sent close together run into one, and the chips latch a garbled mix.
#: The low *before* the data gives the line a clean start, so a stray high edge when the
#: SPI controller starts a transfer cannot be read as an extra 1 bit -- which would shift
#: every colour on the bar by one bit, showing first as LED 0 flashing green (G is the
#: first byte).  Found on the Pi 2026-11-02 when the app began sending frames close
#: together; 60 us before, after only.
RESET_US = 300
SPI_HZ = 3_200_000
ZERO_PATTERN = 0b1000
ONE_PATTERN = 0b1110


def _table(zero=ZERO_PATTERN, one=ONE_PATTERN):
    """byte -> 4 bytes of SPI, MSB first, two WS2812 bits per SPI byte."""
    out = []
    for value in range(256):
        bits = [(value >> (7 - i)) & 1 for i in range(8)]
        nibbles = [one if b else zero for b in bits]
        out.append(bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, 8, 2)))
    return out


class Strip:
    """A WS2812 strip on SPI0 (MOSI = GPIO10, physical pin 19).

    `order` is the wire order of the chip: WS2812B is GRB, SK6812 RGBW is GRBW
    (this class handles RGB only -- for RGBW add the W byte here, not in callers).

    `mirror` is for a bar mounted with its data input at the C8 end: index 0 still
    means the A0 end to every caller, and only the wire order of pixels is reversed.
    This is a true mirror (i -> n-1-i), unlike upstream's `reverse` (n - pos), which
    is one LED off and folds A0 onto the clamp.
    """

    def __init__(self, n, order="GRB", hz=SPI_HZ, bus=0, device=0,
                 zero=ZERO_PATTERN, one=ONE_PATTERN, brightness=1.0, mirror=False,
                 reset_us=RESET_US):
        self.n = n
        self.order = order.upper()
        self.brightness = brightness
        self.mirror = bool(mirror)
        self._table = _table(zero, one)
        self._reset = bytes(max(1, int(hz * reset_us / 1e6 / 8)))
        self._last = None               # the bytes of the last frame actually sent
        self._lock = None
        self.buf = [(0, 0, 0)] * n
        self.spi = None
        self.hz = hz
        self.bus, self.device = bus, device

    # ------------------------------------------------------------ hardware
    class _Dry:
        """Stands in for spidev so the stages can be rehearsed off the Pi."""

        def __init__(self):
            self.frames = 0
            self.bytes = 0

        def writebytes2(self, data):
            self.frames += 1
            self.bytes += len(data)

        def close(self):
            pass

    def open(self, dry=False):
        self._last = None
        if dry:
            self.spi = Strip._Dry()
            return self
        import spidev

        # one writer per strip: two processes interleaving frames garble the bar
        self._lock = claim_strip(lock_path(self.bus, self.device))
        self.spi = spidev.SpiDev()
        self.spi.open(self.bus, self.device)
        self.spi.max_speed_hz = self.hz
        self.spi.mode = 0
        self.spi.lsbfirst = False
        return self

    def close(self):
        if self.spi is not None:
            self.spi.close()
            self.spi = None
        if self._lock is not None:
            self._lock.close()              # closing the file drops the flock
            self._lock = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        try:
            self.clear()
            self.show()
        finally:
            self.close()

    # --------------------------------------------------------------- pixels
    def __setitem__(self, i, rgb):
        self.buf[i] = tuple(int(v) for v in rgb)

    def __getitem__(self, i):
        return self.buf[i]

    def fill(self, rgb):
        self.buf = [tuple(int(v) for v in rgb)] * self.n

    def clear(self):
        self.fill((0, 0, 0))

    # ---------------------------------------------------------------- frame
    def frame(self):
        """The exact bytes that go out of MOSI for the current buffer: a reset's worth of
        low, the pixels, and another reset."""
        b = self.brightness
        out = bytearray(self._reset)
        for r, g, bl in (reversed(self.buf) if self.mirror else self.buf):
            if b != 1.0:
                r, g, bl = int(r * b), int(g * b), int(bl * b)
            chan = {"R": r & 0xFF, "G": g & 0xFF, "B": bl & 0xFF}
            for name in self.order:
                out += self._table[chan[name]]
        out += self._reset
        return bytes(out)

    def show(self, force=False):
        """Send the buffer.  A frame identical, byte for byte, to the last one sent is
        skipped (returns 0): the strip already shows it, and every transfer is a chance
        for a glitch.  `force=True` sends it anyway."""
        if self.spi is None:
            raise RuntimeError("strip not open -- use `with Strip(n) as s:` or call open()")
        data = self.frame()
        if data == self._last and not force:
            return 0
        # writebytes2 chunks internally and avoids the 4096-byte list limit
        self.spi.writebytes2(data)
        self._last = data
        return len(data)


def lock_path(bus=0, device=0):
    base = "/run/lock" if os.path.isdir("/run/lock") else tempfile.gettempdir()
    return os.path.join(base, "fingerlight-spi%d.%d.lock" % (bus, device))


def claim_strip(path):
    """Take the strip for this process, or say plainly who has it.  The lock lives as
    long as the returned file is open (and dies with the process)."""
    import fcntl

    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        who = fh.read().strip() or "another process"
        fh.close()
        raise RuntimeError(
            "the LED strip is already in use by %s -- only one program may drive it. "
            "If that is the app: sudo systemctl stop fingerlight" % who)
    fh.seek(0)
    fh.truncate()
    fh.write("pid %d (%s)" % (os.getpid(), os.path.basename(sys.argv[0] or "python")))
    fh.flush()
    return fh


# ------------------------------------------------------------------ selftest
def selftest():
    """Check the encoder without a Pi. Every expectation here is derived by hand
    from the bit patterns in the module docstring, not from the code."""
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print("%-34s %-4s got %s want %s"
              % (name, "PASS" if good else "FAIL", got, want))

    t = _table()
    # 0x00 -> eight zero-bits -> 1000 1000 per SPI byte -> 0x88 x4
    check("byte 0x00 encodes", t[0x00].hex(), "88888888")
    # 0xFF -> eight one-bits  -> 1110 1110 -> 0xEE x4
    check("byte 0xFF encodes", t[0xFF].hex(), "eeeeeeee")
    # 0x01 -> 0000 0001 -> 88 88 88 8E
    check("byte 0x01 encodes", t[0x01].hex(), "8888888e")
    # 0x80 -> 1000 0000 -> E8 88 88 88
    check("byte 0x80 encodes", t[0x80].hex(), "e8888888")

    s = Strip(3)
    s[0] = (255, 0, 0)
    s[1] = (0, 255, 0)
    s[2] = (0, 0, 255)
    full = s.frame()
    lead = len(s._reset)
    check("reset is >= 280 us of low", lead * 8 / SPI_HZ * 1e6 >= 280, True)
    check("frame starts and ends low", (full[:lead], full[-lead:]),
          (bytes(lead), bytes(lead)))
    check("frame length, 3 LEDs", len(full), 3 * 3 * 4 + 2 * lead)
    f = full[lead:]
    # WS2812B is GRB: a red pixel sends G=0x00, then R=0xFF, then B=0x00
    check("red pixel bytes", f[0:12].hex(), "88888888" + "eeeeeeee" + "88888888")
    check("green pixel bytes", f[12:24].hex(), "eeeeeeee" + "88888888" + "88888888")
    check("blue pixel bytes", f[24:36].hex(), "88888888" + "88888888" + "eeeeeeee")

    s176 = Strip(176)
    s176.fill((255, 255, 255))
    n = len(s176.frame())
    check("176-LED frame fits spidev", n <= 4096, True)
    print("   176 LEDs, full white -> %d bytes (spidev default limit 4096)" % n)

    s176.brightness = 0.5
    half = s176.frame()
    check("brightness scales the wire", half[lead:lead + 4].hex(), _table()[127].hex())

    # mirror: the same red/green/blue buffer goes out blue, green, red
    m = Strip(3, mirror=True)
    m[0] = (255, 0, 0)
    m[1] = (0, 255, 0)
    m[2] = (0, 0, 255)
    check("mirror reverses the wire", m.frame()[lead:lead + 36].hex(), f[24:36].hex() + f[12:24].hex()
          + f[0:12].hex())
    check("mirror leaves the buffer", m[0], (255, 0, 0))

    # an unchanged frame is not re-sent
    d = Strip(3).open(dry=True)
    d[0] = (9, 9, 9)
    sent = [d.show(), d.show(), d.show(force=True)]
    check("identical frame skipped", (sent[1], d.spi.frames), (0, 2))

    print("\nSELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true", help="check the encoder, no Pi needed")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
