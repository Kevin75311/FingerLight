"""Tests for free play: which keys light, in what colour, and how they fade.

Time is passed in, so every fade is checked at exact instants.  No strip, no MIDI.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.colors import BLACK_KEY_GAIN, scale  # noqa: E402
from fingerlight.freeplay import (  # noqa: E402
    STYLES,
    VELOCITY_RAMP,
    FreePlay,
    Look,
    parse_hex,
)
from fingerlight.notemap import StripSettings, note_to_leds  # noqa: E402

STRIP = StripSettings.load()
SOLID = Look(style="solid", color=(200, 100, 50), fade_ms=400)


def test_a_held_key_lights_both_leds_of_its_pair_in_the_chosen_colour():
    fp = FreePlay()
    fp.note_on(60, 100, t=0.0)
    px = fp.frame(0.0, SOLID, STRIP)
    lo, hi = note_to_leds(60, STRIP)
    assert px == {lo: (200, 100, 50), hi: (200, 100, 50)}


def test_release_fades_quadratically_then_goes_dark_and_is_pruned():
    fp = FreePlay()
    fp.note_on(60, 100, t=0.0)
    fp.note_off(60, t=1.0)
    assert fp.level(60, 1.0, SOLID) == 1.0
    assert fp.level(60, 1.2, SOLID) == pytest.approx(0.25)      # half-way: (1 - 0.5)^2
    assert fp.fading(1.2, SOLID)
    assert fp.level(60, 1.4, SOLID) == 0.0
    assert not fp.fading(1.4, SOLID)
    assert fp.prune(1.4, SOLID) and fp.lit == {}
    assert fp.frame(1.4, SOLID, STRIP) == {}


def test_fade_off_means_dark_on_release():
    fp = FreePlay()
    look = Look(fade_ms=0)
    fp.note_on(60, t=0.0)
    fp.note_off(60, t=0.5)
    assert fp.frame(0.5, look, STRIP) == {}


def test_the_pedal_holds_released_keys_until_it_comes_up():
    fp = FreePlay()
    fp.set_pedal(True, t=0.0)
    fp.note_on(60, t=0.0)
    fp.note_off(60, t=0.1)
    assert fp.level(60, 5.0, SOLID) == 1.0                    # still sounding
    fp.set_pedal(False, t=5.0)
    assert fp.level(60, 5.2, SOLID) == pytest.approx(0.25)    # the fade starts at pedal-up
    assert fp.level(60, 5.4, SOLID) == 0.0


def test_a_key_still_held_at_pedal_up_stays_lit():
    fp = FreePlay()
    fp.set_pedal(True, t=0.0)
    fp.note_on(60, t=0.0)
    fp.set_pedal(False, t=1.0)
    assert fp.level(60, 9.0, SOLID) == 1.0


def test_sustain_off_ignores_the_pedal():
    fp = FreePlay()
    look = Look(fade_ms=400, sustain=False)
    fp.set_pedal(True, t=0.0)
    fp.note_on(60, t=0.0)
    fp.note_off(60, t=1.0, sustain=look.sustain)
    assert fp.level(60, 1.2, look) == pytest.approx(0.25)


def test_restriking_a_fading_key_brings_it_back_to_full():
    fp = FreePlay()
    fp.note_on(60, t=0.0)
    fp.note_off(60, t=1.0)
    fp.note_on(60, t=1.2)
    assert fp.level(60, 3.0, SOLID) == 1.0


def test_split_style_picks_by_side_of_the_split_key():
    look = Look(style="split", color=(255, 0, 0), color2=(0, 255, 0), split=60)
    assert look.color_for(59 - 2, 100) == (255, 0, 0)          # A3: white key, left side
    assert look.color_for(60, 100) == (0, 255, 0)               # the split key is right
    assert look.color_for(62, 100) == (0, 255, 0)


def test_black_keys_get_the_firmware_gain():
    look = Look(style="solid", color=(100, 50, 20))
    assert look.color_for(61, 100) == scale((100, 50, 20), BLACK_KEY_GAIN)


def test_rainbow_ends_differ_and_velocity_is_ignored():
    look = Look(style="rainbow")
    assert look.color_for(21, 10) == look.color_for(21, 127) == (255, 0, 0)
    assert look.color_for(108, 100) != look.color_for(21, 100)


def test_velocity_style_soft_is_dim_teal_hard_is_full_red():
    look = Look(style="velocity")
    soft, hard = look.color_for(60, 0), look.color_for(60, 127)
    assert hard == VELOCITY_RAMP[-1]
    assert soft == scale(VELOCITY_RAMP[0], 0.30)
    assert sum(soft) < sum(look.color_for(60, 64)) < sum(hard) + 255


def test_shared_leds_take_the_brighter_channel_not_the_last_key():
    fp = FreePlay()
    look = Look(style="split", color=(255, 0, 0), color2=(0, 0, 255), split=61)
    # 60 and 61 sit ~1.97 LEDs apart, so their pairs may share an LED; wherever they
    # do, the shared pixel must carry both colours
    fp.note_on(60, t=0.0)
    fp.note_on(61, t=0.0)
    px = fp.frame(0.0, look, STRIP)
    shared = set(note_to_leds(60, STRIP)) & set(note_to_leds(61, STRIP))
    for i in shared:
        assert px[i][0] == 255 and px[i][2] > 0


def test_offsets_from_calibration_move_the_light():
    fp = FreePlay()
    fp.note_on(60, t=0.0)
    moved = STRIP.with_offsets({60: 3})
    lo, _ = note_to_leds(60, STRIP)
    assert min(fp.frame(0.0, SOLID, moved)) == lo + 3


def test_parse_hex():
    assert parse_hex("#ffb060") == (255, 176, 96)
    for bad in ("ffb060", "#fff", "#gg0000", ""):
        with pytest.raises(ValueError):
            parse_hex(bad)


def test_every_style_has_a_colour_for_every_key():
    for style in STYLES:
        look = Look(style=style)
        for p in range(21, 109):
            assert all(0 <= c <= 255 for c in look.color_for(p, 64))
