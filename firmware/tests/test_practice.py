"""Tests for the light-score reader and the practice state machine (A0 consumer, B3).

Driven by the real fixtures in `lightscore/examples/`, not by synthetic data invented
here -- so these tests also assert that the Part 3 contract is actually consumable by
Part 2, which is the thing a schema alone cannot prove.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.lightscore import (  # noqa: E402
    LightScoreError,
    group_events,
    load,
    parse,
)
from fingerlight.notemap import StripSettings, note_to_leds  # noqa: E402
from fingerlight.practice import PracticeSession  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCALE = os.path.join(_ROOT, "lightscore", "examples", "c-major-scale-rh.lightscore.json")
CHORDS = os.path.join(_ROOT, "lightscore", "examples", "two-hand-chords.lightscore.json")


@pytest.fixture(scope="module")
def settings():
    return StripSettings.load()


@pytest.fixture
def scale_score():
    return load(SCALE)


@pytest.fixture
def chord_score():
    return load(CHORDS)


# ------------------------------------------------------------------ the reader


def test_scale_fixture_loads(scale_score):
    assert len(scale_score.notes) == 8
    assert len(scale_score.events) == 8, "a scale is 8 separate events"
    assert [n.p for n in scale_score.notes] == [60, 62, 64, 65, 67, 69, 71, 72]
    assert [n.f for n in scale_score.notes] == [1, 2, 3, 1, 2, 3, 4, 5]
    assert scale_score.duration_ms == 4000


def test_chord_fixture_groups_into_events(chord_score):
    assert len(chord_score.notes) == 21
    assert len(chord_score.events) == 4, "four block chords"
    assert [len(e.notes) for e in chord_score.events] == [5, 5, 6, 5]
    hands = {n.h for n in chord_score.notes}
    assert hands == {"L", "R"}, "fixture must exercise both hands"


def test_events_are_sorted_and_grouped_exactly(chord_score):
    ts = [e.t for e in chord_score.events]
    assert ts == sorted(ts) == [0, 1000, 2000, 3000]


@pytest.mark.parametrize(
    "mutate, expect",
    [
        (lambda d: d.update(format="notalightscore"), "bad 'format'"),
        (lambda d: d.update(version=2), "unsupported version"),
        (lambda d: d.update(notes=[]), "no notes"),
        (lambda d: d["notes"][0].update(p=20), "outside the 88-key range"),
        (lambda d: d["notes"][0].update(p=109), "outside the 88-key range"),
        (lambda d: d["notes"][0].update(f=0), "bad finger"),
        (lambda d: d["notes"][0].update(f=6), "bad finger"),
        (lambda d: d["notes"][0].update(h="X"), "bad hand"),
        (lambda d: d["notes"][0].update(d=0), "bad duration"),
        (lambda d: d["notes"][0].update(t=-1), "bad onset"),
    ],
)
def test_reader_rejects_malformed_scores(mutate, expect):
    """The reader must fail at load time, not halfway through a session."""
    doc = json.load(open(SCALE, encoding="utf-8"))
    mutate(doc)
    with pytest.raises(LightScoreError) as exc:
        parse(doc)
    assert expect in str(exc.value)


def test_pitch_bound_matches_the_bar():
    """Out-of-range pitches are rejected because the bar physically cannot show them."""
    doc = json.load(open(SCALE, encoding="utf-8"))
    doc["notes"][0]["p"] = 21   # A0, the lowest key
    assert parse(doc).notes[0].p == 21
    doc["notes"][0]["p"] = 108  # C8, the highest
    assert parse(doc).notes[0].p == 108


# --------------------------------------------------------- the practice machine


def test_playing_the_scale_correctly_advances_to_the_end(scale_score, settings):
    s = PracticeSession(scale_score, settings)
    assert s.progress() == (0, 8)
    for note in scale_score.notes:
        assert s.note_on(note.p) is True, "correct note rejected: %d" % note.p
        s.note_off(note.p)
    assert s.finished
    assert s.errors == 0
    assert s.progress() == (8, 8)


def test_wrong_note_is_counted_and_does_not_advance(scale_score, settings):
    s = PracticeSession(scale_score, settings)
    assert s.note_on(61) is False, "C#4 is not in this scale"
    assert s.errors == 1
    assert s.index == 0, "a wrong note must not advance the cursor"
    assert s.wrong_notes == [(0, 61)]
    # and the right note still works afterwards
    assert s.note_on(60) is True
    assert s.index == 1


def test_chord_advances_only_when_every_note_is_held(chord_score, settings):
    s = PracticeSession(chord_score, settings)
    first = chord_score.events[0]
    pitches = sorted(first.pitches)
    for p in pitches[:-1]:
        s.note_on(p)
        assert s.index == 0, "advanced on a partial chord"
    assert s.waiting_for == frozenset({pitches[-1]})
    s.note_on(pitches[-1])
    assert s.index == 1, "did not advance on a completed chord"
    assert s.errors == 0


def test_repeated_strike_of_a_held_chord_tone_is_not_an_error(chord_score, settings):
    s = PracticeSession(chord_score, settings)
    p = sorted(chord_score.events[0].pitches)[0]
    s.note_on(p)
    s.note_on(p)
    assert s.errors == 0
    assert s.index == 0


def test_release_does_not_undo_progress(chord_score, settings):
    """Rolling a chord -- releasing the bottom note before striking the top -- is
    normal playing, not a mistake."""
    s = PracticeSession(chord_score, settings)
    pitches = sorted(chord_score.events[0].pitches)
    for p in pitches[:-1]:
        s.note_on(p)
        s.note_off(p)
    assert s.waiting_for == frozenset({pitches[-1]})
    s.note_on(pitches[-1])
    assert s.index == 1


def test_skip_and_reset(scale_score, settings):
    s = PracticeSession(scale_score, settings)
    s.skip()
    assert s.index == 1
    s.note_on(61)
    assert s.errors == 1
    s.reset()
    assert (s.index, s.errors, s.wrong_notes) == (0, 0, [])


def test_seek_clamps_drops_partial_credit_and_keeps_errors(chord_score, settings):
    s = PracticeSession(chord_score, settings)
    first = sorted(chord_score.events[0].pitches)
    s.note_on(first[0])                     # partial credit on event 0
    s.note_on(20 + 1)                       # A0 is in no chord here: one error
    s.seek(2)
    assert s.index == 2 and s.errors == 1
    assert s.waiting_for == chord_score.events[2].pitches, "partial credit leaked"
    s.seek(0)
    assert s.waiting_for == chord_score.events[0].pitches
    s.seek(99)
    assert s.finished and s.index == len(chord_score.events)
    s.seek(-5)
    assert s.index == 0


# --------------------------------------------------------------------- rendering


def test_frame_lights_the_current_event(scale_score, settings):
    s = PracticeSession(scale_score, settings, preview=0)
    f = s.frame()
    lo, hi = note_to_leds(60, settings)   # C4, the first note
    assert lo in f.pixels and hi in f.pixels
    assert f.waiting_for == frozenset({60})
    assert not f.finished


def test_preview_is_dimmer_than_now(scale_score, settings):
    """The current event must be unmistakable against the preview."""
    s = PracticeSession(scale_score, settings, preview=1)
    f = s.frame()
    now_lo, now_hi = note_to_leds(60, settings)    # current
    nxt_lo, nxt_hi = note_to_leds(62, settings)    # previewed
    assert sum(f.pixels[now_hi]) > sum(f.pixels[nxt_hi]), (
        "preview is not dimmer than the current event: %r vs %r"
        % (f.pixels[now_hi], f.pixels[nxt_hi])
    )


def test_current_event_wins_a_pixel_collision(scale_score, settings):
    """Adjacent keys share LEDs at 144 LED/m, so current and preview WILL collide.
    When they do, 'now' must win -- otherwise the player is cued to the wrong key."""
    s = PracticeSession(scale_score, settings, preview=3)
    f = s.frame()
    now_lo, now_hi = note_to_leds(60, settings)
    full = pytest.approx(sum(f.pixels[now_hi]), rel=0)
    # Re-render with no preview at all; the current pixels must be identical.
    s_no_preview = PracticeSession(scale_score, settings, preview=0)
    assert f.pixels[now_hi] == s_no_preview.frame().pixels[now_hi]


def test_satisfied_chord_tone_dims_but_stays_lit(chord_score, settings):
    s = PracticeSession(chord_score, settings, preview=0)
    pitches = sorted(chord_score.events[0].pitches)
    before = s.frame().pixels
    s.note_on(pitches[0])
    after = s.frame().pixels
    lo, hi = note_to_leds(pitches[0], settings)
    assert hi in after, "a satisfied chord tone vanished; it should dim, not disappear"
    assert sum(after[hi]) < sum(before[hi])


def test_finished_frame_is_dark(scale_score, settings):
    s = PracticeSession(scale_score, settings)
    for n in scale_score.notes:
        s.note_on(n.p)
    f = s.frame()
    assert f.finished
    assert f.pixels == {}


def test_every_pixel_index_is_inside_the_strip(chord_score, settings):
    s = PracticeSession(chord_score, settings, preview=2)
    while not s.finished:
        for idx in s.frame().pixels:
            assert 0 <= idx < settings.led_count, "pixel %d off the strip" % idx
        for p in sorted(s.current.pitches):
            s.note_on(p)
