"""Regression tests for **left-hand mirroring**.

The first version of the DP fingered the left hand as though it were a right hand:
thumb on the lowest note, little finger on the highest.  That is backwards -- the left
hand is the mirror image of the right, with the thumb at the *high* end of what it
holds.  Nothing errored; the output was simply wrong for every left-hand note (13 of 21
notes on the two-hand fixture).

These tests exist because that defect is invisible to every structural check: the notes
were present, the fingers were all in 1..5, and no finger was reused within a chord.
Only the *orientation* was wrong, so only an orientation assertion can catch it.
"""

import json
import os
import sys
from collections import defaultdict

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight_score.fingering import assign_fingering  # noqa: E402
from fingerlight_score.notes import Note  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHORDS = os.path.join(_ROOT, "lightscore", "examples", "two-hand-chords.lightscore.json")


def _load_fixture_notes():
    doc = json.load(open(CHORDS, encoding="utf-8"))
    notes = [
        Note(t_ms=n["t"], d_ms=n["d"], pitch=n["p"], staff=n["stf"])
        for n in doc["notes"]
    ]
    return doc, notes


def _groups(fingered):
    """Simultaneous notes, split by hand."""
    out = defaultdict(list)
    for fn in fingered:
        out[(fn.t_ms, fn.hand)].append(fn)
    return {k: sorted(v, key=lambda n: n.pitch) for k, v in out.items()}


def test_left_hand_thumb_is_on_the_highest_note():
    """LH: finger number must *fall* as pitch rises.  RH: it must rise."""
    _, notes = _load_fixture_notes()
    fingered = assign_fingering(notes)
    checked = 0
    for (t, hand), group in sorted(_groups(fingered).items()):
        if len(group) < 2:
            continue
        fingers = [n.finger for n in group]   # pitch-ascending
        checked += 1
        if hand == "R":
            assert fingers == sorted(fingers), (
                "RH at t=%d: fingers %r should rise with pitch" % (t, fingers)
            )
        else:
            assert fingers == sorted(fingers, reverse=True), (
                "LH at t=%d: fingers %r should FALL with pitch (thumb on top)"
                % (t, fingers)
            )
    # Print-the-N guard: "all groups OK" must not mean "no groups examined".
    assert checked == 8, "expected 8 multi-note hand groups, examined %d" % checked


def test_left_hand_lowest_note_gets_a_high_finger():
    """The concrete symptom of the old bug: pinky, not thumb, on the bottom note."""
    _, notes = _load_fixture_notes()
    fingered = assign_fingering(notes)
    for (t, hand), group in _groups(fingered).items():
        if hand != "L" or len(group) < 2:
            continue
        assert group[0].finger >= 4, (
            "LH at t=%d: lowest note (pitch %d) got finger %d; expected 4 or 5"
            % (t, group[0].pitch, group[0].finger)
        )
        assert group[-1].finger <= 2, (
            "LH at t=%d: highest note (pitch %d) got finger %d; expected 1 or 2"
            % (t, group[-1].pitch, group[-1].finger)
        )


def test_a_descending_left_hand_line_mirrors_the_right():
    """A single-line LH scale should mirror the RH fingering, not copy it."""
    pitches = [48, 50, 52, 53, 55, 57, 59, 60]  # C3 major ascending, left hand
    notes = [
        Note(t_ms=i * 500, d_ms=500, pitch=p, staff=2)
        for i, p in enumerate(pitches)
    ]
    fingers = [f.finger for f in assign_fingering(notes)]
    assert all(f.hand == "L" for f in assign_fingering(notes))
    # Ascending in the LEFT hand starts from the little finger and works toward the
    # thumb, the reverse of the right hand's 1-2-3-1-2-3-4-5.
    assert fingers[0] >= 4, "LH ascending should start on 4 or 5, got %r" % (fingers,)
    assert fingers[-1] <= 2, "LH ascending should end near the thumb, got %r" % (fingers,)
    assert 1 <= min(fingers) and max(fingers) <= 5


def test_no_finger_reused_within_one_hand_and_event():
    _, notes = _load_fixture_notes()
    fingered = assign_fingering(notes)
    for (t, hand), group in _groups(fingered).items():
        fingers = [n.finger for n in group]
        assert len(set(fingers)) == len(fingers), (
            "%s at t=%d reuses a finger: %r" % (hand, t, fingers)
        )


def test_every_note_keeps_its_pitch_and_gets_a_legal_finger():
    doc, notes = _load_fixture_notes()
    fingered = assign_fingering(notes)
    assert len(fingered) == len(notes)
    assert [f.pitch for f in fingered] == [n["p"] for n in doc["notes"]]
    assert all(1 <= f.finger <= 5 for f in fingered)
    assert {f.hand for f in fingered} == {"L", "R"}
