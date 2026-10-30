"""The bar can be mounted with its data input at the C8 end.  `Strip(mirror=True)` must
put every key's pair at the mirrored place *on the wire*, while callers keep indexing
from A0.  Checked on the encoded bytes, the only thing the strip ever sees."""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hw"))
sys.path.insert(0, os.path.join(ROOT, "firmware", "src"))

import smoke  # noqa: E402
from fingerlight.notemap import StripSettings, note_to_leds  # noqa: E402
from ws2812_spi import Strip  # noqa: E402


def lit_on_wire(strip):
    """Wire positions (0 = first LED after the Pi) whose 12 bytes are not all-zero."""
    f = strip.frame()
    zero = strip._table[0] * 3
    return [i for i in range(strip.n) if f[i * 12:(i + 1) * 12] != zero]


def test_mirror_puts_a0_at_the_far_end_and_c8_at_the_near_end():
    s = StripSettings.load()
    n = s.led_count
    for note in (21, 60, 108):
        lo, hi = note_to_leds(note, s)
        plain, mirrored = Strip(n), Strip(n, mirror=True)
        for strip in (plain, mirrored):
            strip[lo] = strip[hi] = (255, 255, 255)
        assert lit_on_wire(plain) == sorted({lo, hi})
        assert lit_on_wire(mirrored) == sorted({n - 1 - lo, n - 1 - hi})
    # A0 and C8 each keep two distinct LEDs, both inside the strip, at opposite ends
    a0 = Strip(n, mirror=True)
    for i in note_to_leds(21, s):
        a0[i] = (255, 0, 0)
    assert lit_on_wire(a0) == [n - 2, n - 1]


def test_mirror_is_the_exact_reverse_of_the_plain_frame():
    plain, mirrored = Strip(5), Strip(5, mirror=True)
    for i, rgb in enumerate([(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15)]):
        plain[i] = rgb
        mirrored[4 - i] = rgb
    assert plain.frame() == mirrored.frame()


def test_bench_tools_follow_the_app_setting(tmp_path):
    assert smoke.saved_mirror(str(tmp_path)) is False          # no settings file
    (tmp_path / "settings.json").write_text(json.dumps({"led_mirror": True}))
    assert smoke.saved_mirror(str(tmp_path)) is True
    (tmp_path / "settings.json").write_text("not json")
    assert smoke.saved_mirror(str(tmp_path)) is False
