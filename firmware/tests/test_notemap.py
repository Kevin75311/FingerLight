"""Tests for the note -> LED map.

The point of these tests is decision D-08: prove that *upstream's own function*, fed the
settings `tools/gen_map.py` generates, reproduces the geometric map exactly -- so the
correction ships as settings, not as a driver fork.

They run on a PC.  No Pi, no LED strip, no `rpi_ws281x`.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.notemap import (  # noqa: E402
    DEFAULT_SETTINGS,
    MIDI_MAX,
    MIDI_MIN,
    StripSettings,
    build_table,
    get_note_position,
    load_map_rows,
    note_to_leds,
    to_upstream_settings_xml,
    verify_against_map,
)


@pytest.fixture(scope="module")
def settings():
    return StripSettings.load()


@pytest.fixture(scope="module")
def rows():
    return load_map_rows()


# --------------------------------------------------------------- the headline test


def test_generated_settings_reproduce_the_map(settings, rows):
    """All 88 notes: upstream's formula == the geometric map.  This is D-08."""
    chk = verify_against_map(settings, rows)
    assert chk.checked == 88, chk.summary()
    assert not chk.mismatches, "first mismatches: %r" % (chk.mismatches[:5],)
    assert chk.ok, chk.summary()


def test_the_check_can_actually_fail(rows):
    """A check that cannot fail is not evidence.

    Deliberately wrong settings must be caught -- otherwise the test above proves
    nothing about the real ones.
    """
    wrong = StripSettings(shift=3, note_offsets=((92, 2), (55, 1)))
    chk = verify_against_map(wrong, rows)
    assert chk.mismatches, "sabotaged settings passed -- the check is inert"


def test_naive_uniform_formula_overruns_the_strip(rows):
    """With no offsets at all, the uniform assumption asks for LED 176 on a 176-LED
    strip (indices 0..175) for C8.

    Measured, not assumed: this is the `naive_led` column of `data/led_map.csv`.
    """
    naive = StripSettings(shift=0, note_offsets=())
    chk = verify_against_map(naive, rows)
    assert (MIDI_MAX, 176) in chk.out_of_range, (
        "expected C8 -> LED 176; got %r" % (chk.out_of_range,)
    )


def test_upstream_defaults_collide_two_notes(rows):
    """Upstream's *stock* offsets stay in range -- but they are still wrong, in a way
    that is worse than an overrun because nothing errors: **G#6 and A6 land on the same
    LED** and become indistinguishable, and the top of the board drifts 2 LEDs (~14 mm).

    Pinned as a regression test because this is the concrete defect D-08 fixes, and
    because it corrects HANDOFF 4.2, which attributes the LED-176 overrun to these
    defaults -- the overrun belongs to the no-offset formula above.
    """
    stock = StripSettings(shift=0, note_offsets=((92, 2), (55, 1)))
    chk = verify_against_map(stock, rows)
    assert not chk.out_of_range, "defaults were expected to stay in range"
    assert chk.duplicate_leds == {143: [92, 93]}, (
        "expected G#6/A6 to collide on LED 143; got %r" % (chk.duplicate_leds,)
    )
    worst = max(abs(int(r["primary_led"]) - get_note_position(int(r["midi"]), stock))
                for r in rows)
    assert worst == 2, "expected a 2-LED worst-case drift, got %d" % worst


# ------------------------------------------------------------------- basic soundness


def test_every_key_has_a_distinct_led(settings, rows):
    chk = verify_against_map(settings, rows)
    assert not chk.duplicate_leds, (
        "two notes share an LED and would be indistinguishable: %r"
        % (chk.duplicate_leds,)
    )


def test_all_indices_inside_the_strip(settings):
    for note in range(MIDI_MIN, MIDI_MAX + 1):
        led = get_note_position(note, settings)
        assert 0 <= led < settings.led_count, "note %d -> LED %d" % (note, led)


def test_mapping_is_monotonic(settings):
    """Pitch rises left to right; the map must never go backwards."""
    prev = -1
    for note in range(MIDI_MIN, MIDI_MAX + 1):
        led = get_note_position(note, settings)
        assert led >= prev, "note %d went backwards: %d after %d" % (note, led, prev)
        prev = led


def test_table_matches_the_function(settings):
    table = build_table(settings)
    assert len(table) == 88
    assert all(table[n] == get_note_position(n, settings) for n in table)


# ------------------------------------------------------------------ the 2-LED pair


def test_led_pair_is_adjacent_and_in_range(settings):
    """D-12: left pixel = hand, right pixel = finger.  Both must exist."""
    for note in range(MIDI_MIN, MIDI_MAX + 1):
        lo, hi = note_to_leds(note, settings)
        assert 0 <= lo < settings.led_count
        assert 0 <= hi < settings.led_count
        assert hi - lo in (0, 1), "pair not adjacent for note %d: %r" % (note, (lo, hi))


def test_top_key_pair_is_clamped_not_overrun(settings):
    """C8 is the key whose pair would run off the end if left unclamped."""
    lo, hi = note_to_leds(MIDI_MAX, settings)
    assert hi < settings.led_count


# ------------------------------------------------------------------ provenance guards


def test_overlay_matches_the_generated_map_provenance(settings, rows):
    """The overlay records the octave span it was generated from.

    If someone reruns gen_map.py at a new measurement but forgets the overlay, the map
    and the settings drift apart silently.  This catches exactly that.
    """
    with open(DEFAULT_SETTINGS, encoding="utf-8") as fh:
        raw = json.load(fh)
    assert raw["octave_span_mm"] == settings.octave_span_mm
    # And the settings must still reproduce whatever map is currently on disk.
    assert verify_against_map(settings, rows).ok, (
        "settings_overlay.json and data/led_map.csv disagree -- one of them is stale; "
        "rerun `python tools/gen_map.py %s`" % raw["octave_span_mm"]
    )


def test_settings_xml_fragment_roundtrips(settings):
    xml = to_upstream_settings_xml(settings)
    assert "<note_offsets>[[21, -1], [51, 1], [96, 1], [107, -1]]</note_offsets>" in xml
    assert "<led_count>176</led_count>" in xml


# ------------------------------------------------------- per-key calibration offsets


def test_offsets_move_one_key_and_leave_the_rest_and_upstream_alone():
    s = StripSettings.load()
    moved = s.with_offsets({60: 2, 61: 0, 62: -1})
    assert moved.led_offsets == ((60, 2), (62, -1))      # zeros dropped, sorted
    lo, hi = note_to_leds(60, s)
    assert note_to_leds(60, moved) == (lo + 2, hi + 2)
    assert note_to_leds(62, moved)[0] == note_to_leds(62, s)[0] - 1
    for n in range(MIDI_MIN, MIDI_MAX + 1):
        if n not in (60, 62):
            assert note_to_leds(n, moved) == note_to_leds(n, s)
        # upstream's formula is untouched, so the D-08 check still checks the map
        assert get_note_position(n, moved) == get_note_position(n, s)
    assert verify_against_map(moved).ok


def test_an_offset_past_the_end_is_still_clamped_onto_the_strip():
    s = StripSettings.load().with_offsets({108: 5, 21: -5})
    assert note_to_leds(108, s) == (s.led_count - 1, s.led_count - 1)
    assert note_to_leds(21, s)[0] == 0
