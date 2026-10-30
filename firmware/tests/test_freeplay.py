"""Tests for free play: which keys light, in what colour, and how they fade.

Time is passed in, so every fade is checked at exact instants.  No strip, no MIDI.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.colors import BLACK_KEY_GAIN, scale  # noqa: E402
from fingerlight.freeplay import (  # noqa: E402
    DECAY_MS,
    PEDAL_FADE_MIN_MS,
    PEDAL_FADE_X,
    STRIKES,
    STYLES,
    VELOCITY_RAMP,
    FreePlay,
    Look,
    parse_hex,
)
from fingerlight.notemap import StripSettings, note_to_leds  # noqa: E402

STRIP = StripSettings.load()
SOLID = Look(style="solid", color=(200, 100, 50), fade_ms=400, strike="steady")


def test_a_held_key_lights_both_leds_of_its_pair_in_the_chosen_colour():
    fp = FreePlay()
    fp.note_on(60, 100, t=0.0)
    px = fp.frame(0.0, SOLID, STRIP)
    lo, hi = note_to_leds(60, STRIP)
    assert px == {lo: (200, 100, 50), hi: (200, 100, 50)}


def test_release_fades_quadratically_then_goes_dark_and_is_pruned():
    fp = FreePlay()
    fp.note_on(60, 100, t=0.0)
    fp.note_off(60, 1.0, SOLID)
    assert fp.level(60, 1.0, SOLID) == 1.0
    assert fp.level(60, 1.2, SOLID) == pytest.approx(0.25)      # half-way: (1 - 0.5)^2
    assert fp.level(60, 1.4, SOLID) == 0.0
    assert fp.prune(1.4, SOLID) and fp.lit == {}
    assert fp.frame(1.4, SOLID, STRIP) == {}


def test_fade_off_means_dark_on_release():
    fp = FreePlay()
    look = Look(fade_ms=0, strike="steady")
    fp.note_on(60, t=0.0)
    fp.note_off(60, 0.5, look)
    assert fp.frame(0.5, look, STRIP) == {}


def test_a_held_key_eases_from_full_to_its_strike_floor():
    fp = FreePlay()
    soft, flash = Look(strike="soft"), Look(strike="flash")
    fp.note_on(60, t=0.0)
    assert fp.level(60, 0.0, soft) == 1.0
    half = DECAY_MS / 2000.0
    assert fp.level(60, half, soft) == pytest.approx(0.40 + 0.60 * 0.25)
    assert fp.level(60, 5.0, soft) == pytest.approx(STRIKES["soft"])
    assert fp.level(60, 5.0, flash) == pytest.approx(STRIKES["flash"])
    assert fp.level(60, 5.0, SOLID) == 1.0                      # steady never dims


def test_release_fades_from_the_glow_not_from_full():
    fp = FreePlay()
    soft = Look(strike="soft", fade_ms=400)
    fp.note_on(60, t=0.0)
    fp.note_off(60, 5.0, soft)                                  # settled at 0.40
    assert fp.level(60, 5.0, soft) == pytest.approx(0.40)
    assert fp.level(60, 5.2, soft) == pytest.approx(0.40 * 0.25)


def test_pedal_down_makes_releases_fade_longer():
    fp = FreePlay()
    fp.set_pedal(True, 0.0, SOLID)
    fp.note_on(60, t=0.0)
    fp.note_off(60, 1.0, SOLID)
    long_s = max(400 * PEDAL_FADE_X, PEDAL_FADE_MIN_MS) / 1000.0
    assert fp.level(60, 1.0 + long_s / 2, SOLID) == pytest.approx(0.25)
    assert fp.level(60, 1.0 + 0.4, SOLID) > 0.5                 # would be dark unpedalled


def test_pedal_up_cuts_the_long_tail_without_a_jump():
    fp = FreePlay()
    fp.set_pedal(True, 0.0, SOLID)
    fp.note_on(60, t=0.0)
    fp.note_off(60, 1.0, SOLID)
    before = fp.level(60, 1.5, SOLID)
    fp.set_pedal(False, 1.5, SOLID)
    assert fp.level(60, 1.5, SOLID) == pytest.approx(before)    # no step
    assert fp.level(60, 1.7, SOLID) == pytest.approx(before * 0.25)
    assert fp.level(60, 1.9, SOLID) == 0.0


def test_pedal_down_stretches_a_fade_already_running():
    fp = FreePlay()
    fp.note_on(60, t=0.0)
    fp.note_off(60, 1.0, SOLID)
    at = fp.level(60, 1.1, SOLID)
    fp.set_pedal(True, 1.1, SOLID)
    assert fp.level(60, 1.1, SOLID) == pytest.approx(at)
    assert fp.level(60, 1.4, SOLID) > 0.0                       # unpedalled it was dark


def test_hold_mode_keeps_released_keys_lit_until_pedal_up():
    fp = FreePlay()
    hold = Look(fade_ms=400, pedal="hold", strike="steady")
    fp.set_pedal(True, 0.0, hold)
    fp.note_on(60, t=0.0)
    fp.note_off(60, 0.1, hold)
    assert fp.level(60, 5.0, hold) == 1.0
    fp.set_pedal(False, 5.0, hold)
    assert fp.level(60, 5.2, hold) == pytest.approx(0.25)
    assert fp.level(60, 5.4, hold) == 0.0


def test_pedal_off_mode_ignores_the_pedal_entirely():
    fp = FreePlay()
    off = Look(fade_ms=400, pedal="off", strike="steady")
    fp.note_on(60, t=0.0)
    fp.note_off(60, 1.0, off)
    fp.set_pedal(True, 1.1, off)
    assert fp.level(60, 1.2, off) == pytest.approx(0.25)        # the fade was not restarted
    fp.note_on(62, t=2.0)
    fp.note_off(62, 2.0, off)
    assert fp.level(62, 2.2, off) == pytest.approx(0.25)


def test_restriking_a_fading_key_brings_it_back_to_full():
    fp = FreePlay()
    fp.note_on(60, t=0.0)
    fp.note_off(60, 1.0, SOLID)
    fp.note_on(60, t=1.2)
    assert fp.level(60, 1.2, Look(strike="soft")) == 1.0


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
