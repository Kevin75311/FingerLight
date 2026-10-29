"""Tests for the finger/hand colour encoding (D-12, D-13, D-17).

These check the things that are *decidable without hardware*: that every palette obeys
the no-pure-blue rule, that no two fingers are near-identical, that the black-key gain is
applied, and that the gain cannot silently destroy a hue by clipping every channel.

What they deliberately do NOT check is whether the palette looks good or reads well in
peripheral vision.  That needs the built bar and Kevin's eyes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.colors import (  # noqa: E402
    BLACK_KEY_GAIN,
    HAND_COLORS,
    PALETTES,
    channel_distance,
    finger_color,
    hand_color,
    has_pure_blue,
    is_black_key,
    min_separation,
    pair_colors,
    scale,
)

ALL_PALETTES = sorted(PALETTES)

#: Manhattan-distance floor below which two emitter colours are treated as too close to
#: tell apart through a diffuser.  A judgement call, not a measurement -- it is set low
#: enough to only catch genuinely near-identical entries.
MIN_SEPARATION_FLOOR = 90


@pytest.mark.parametrize("name", ALL_PALETTES)
def test_no_pure_blue_anywhere(name):
    """D-13: blue is the dimmest channel and dies on a matte black keytop."""
    assert has_pure_blue(name) == [], (
        "palette %r has blue-dominant finger(s): %r" % (name, has_pure_blue(name))
    )


@pytest.mark.parametrize("name", ALL_PALETTES)
def test_palette_is_complete_and_separable(name):
    table = PALETTES[name]
    assert sorted(table) == [1, 2, 3, 4, 5], "palette %r is missing fingers" % name
    dist, pair = min_separation(name)
    assert dist >= MIN_SEPARATION_FLOOR, (
        "palette %r: fingers %r are only %d apart" % (name, pair, dist)
    )


def test_the_separation_check_can_fail():
    """A check that cannot fail is not evidence -- sabotage one entry and confirm."""
    PALETTES["_sabotage"] = dict(PALETTES["ramp"])
    PALETTES["_sabotage"][2] = PALETTES["_sabotage"][1]  # two identical fingers
    try:
        dist, _ = min_separation("_sabotage")
        assert dist < MIN_SEPARATION_FLOOR, "identical colours passed the floor"
    finally:
        del PALETTES["_sabotage"]


def test_black_keys_are_identified():
    # C4=60 white, C#4=61 black, ..., B4=71 white
    assert not is_black_key(60)
    assert is_black_key(61)
    assert not is_black_key(64)   # E
    assert is_black_key(66)       # F#
    assert not is_black_key(71)   # B
    assert is_black_key(22)       # A#0
    assert not is_black_key(108)  # C8


def test_black_key_gain_is_applied():
    """D-17.  A black key must come out brighter than the same finger on a white key."""
    white = finger_color(4, 60)   # C4,  green with headroom
    black = finger_color(4, 61)   # C#4, same finger
    assert sum(black) > sum(white), "black-key gain not applied: %r vs %r" % (black, white)


def test_gain_does_not_wash_out_a_saturated_hue():
    """Scaling clips per channel, so a fully saturated colour shifts hue instead of
    brightening.  Guard that no palette entry becomes near-white under the gain."""
    for name in ALL_PALETTES:
        for finger in range(1, 6):
            if PALETTES[name][finger] == (255, 255, 255):
                continue  # the 'distinct' palette uses white as a deliberate anchor
            r, g, b = finger_color(finger, 61, palette=name)  # black key -> gain
            assert not (r > 200 and g > 200 and b > 200), (
                "palette %r finger %d washes out to near-white under gain: %r"
                % (name, finger, (r, g, b))
            )


def test_brightness_scales_and_clamps():
    assert scale((100, 100, 100), 0.0) == (0, 0, 0)
    assert scale((100, 100, 100), 2.0) == (200, 200, 200)
    assert scale((200, 200, 200), 10.0) == (255, 255, 255), "must clamp at 255"
    assert all(0 <= c <= 255 for c in scale((255, 255, 255), 3.0))


def test_pair_order_matches_the_notemap_pair():
    """`pair_colors` returns (hand, finger); `notemap.note_to_leds` returns (lo, hi).
    The two orders must agree or the bar shows finger-hue on the hand pixel."""
    hand_px, finger_px = pair_colors("R", 1, 60)
    assert hand_px == hand_color("R", 60)
    assert finger_px == finger_color(1, 60)


def test_hands_are_distinguishable_from_each_other():
    assert channel_distance(HAND_COLORS["L"], HAND_COLORS["R"]) >= 60


def test_hand_pixel_stays_dimmer_than_the_finger_pixel():
    """The hand pixel is context; if it outshines the finger hue the encoding inverts."""
    for finger in range(1, 6):
        for hand in ("L", "R"):
            h = sum(hand_color(hand, 60))
            f = sum(finger_color(finger, 60))
            assert h < f, "hand pixel %r outshines finger %d" % (hand, finger)


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        finger_color(0, 60)
    with pytest.raises(ValueError):
        finger_color(6, 60)
    with pytest.raises(ValueError):
        hand_color("X", 60)


def test_gain_constant_is_in_the_documented_band():
    """D-17 states 1.4-1.7x.  If someone measures and changes it, this test is the
    reminder to update the decision log too."""
    assert 1.4 <= BLACK_KEY_GAIN <= 1.7
