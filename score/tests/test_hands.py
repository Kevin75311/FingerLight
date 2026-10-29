"""Tests for A2 (Q-09 hand assignment): ``hands.py``'s "pitch"/"auto" modes, and the
regression proving "staff" mode is untouched by the refactor.

``test_auto_mode_recovers_left_hand_from_staff_stripped_fixture`` is the test that
would have caught the original defect (musicxml.py:361-371): a MusicXML source with
no ``<staff>`` elements at all used to land every note on staff 1, fingering the
whole piece -- bass included -- as a single right hand.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight_score.fingering import assign_fingering  # noqa: E402
from fingerlight_score.hands import (  # noqa: E402
    _smooth_hand_sequence,
    _split_point,
    assign_hands,
    resolve_hand_mode,
)
from fingerlight_score.musicxml import parse_musicxml, musicxml_to_lightscore  # noqa: E402
from fingerlight_score.notes import Note  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
_GRAND_STAFF = os.path.join(_FIXTURES, "grand-staff.musicxml")


def _melody(pitches, step_ms=400, dur_ms=300, staff=1):
    return [Note(t_ms=i * step_ms, d_ms=dur_ms, pitch=p, staff=staff) for i, p in enumerate(pitches)]


# ---------------------------------------------------------------------------
# _split_point: distribution-derived, not a hardcoded middle C
# ---------------------------------------------------------------------------


def test_split_point_is_not_hardcoded_middle_c():
    # Both hands transposed well away from middle C (60): LH cluster ~30-36,
    # RH cluster ~90-96. A hardcoded middle-C split would dump everything in "R".
    notes = _melody([30, 33, 36, 90, 93, 96])
    split = _split_point(notes)
    assert split != 60
    assert 36 < split < 90, "split %r should fall between the two real clusters" % split


def test_split_point_not_fooled_by_a_wide_internal_gap():
    """Regression for the bug found while implementing this: a naive "widest single
    gap between adjacent distinct pitches" heuristic picks the gap *inside* one
    hand's own chord (48->55, 7 semitones) over the true, narrower LH/RH boundary
    gap (55->60, 5 semitones) -- exactly the grand-staff fixture's LH chord C3+G3
    against the RH's C4. 2-means must not make that mistake."""
    lh = [41, 43, 48, 48, 55]  # includes an internal 7-semitone gap (48 -> 55)
    rh = [60, 62, 64, 66, 67, 70]
    notes = [Note(t_ms=i, d_ms=1, pitch=p, staff=1) for i, p in enumerate(lh + rh)]
    split = _split_point(notes)
    assert 55 <= split <= 60, (
        "split %r should land in the true 55..60 boundary gap, not inside the LH "
        "chord's own 48..55 spread" % split
    )


# ---------------------------------------------------------------------------
# Temporal smoothing: no mid-phrase flip on a single note; a sustained register
# change does flip.
# ---------------------------------------------------------------------------


def test_pitch_mode_does_not_flip_on_a_single_outlier_note():
    # An R-hand melody with one isolated low note dipping into LH register.
    notes = _melody([72, 74, 76, 50, 77, 79])
    hands = assign_hands(notes, "pitch")
    result = [hands[id(n)] for n in notes]
    assert result == ["R"] * 6, (
        "a single transient low note must not flip the whole hand assignment: got %r" % result
    )


def test_pitch_mode_flips_for_a_sustained_register_change():
    # Same melody shape, but the low register is sustained across 4 notes -- a real
    # phrase, not a blip -- so it must flip.
    notes = _melody([72, 74, 76, 40, 38, 36, 34])
    hands = assign_hands(notes, "pitch")
    result = [hands[id(n)] for n in notes]
    assert result == ["R", "R", "R", "L", "L", "L", "L"], (
        "a sustained 4-note register change must flip hand: got %r" % result
    )


def test_pitch_mode_does_not_charge_simultaneous_notes_for_differing():
    # A single two-hand chord event (same onset) spanning both registers: must NOT
    # be forced into one hand by the smoothing DP.
    notes = [
        Note(t_ms=0, d_ms=500, pitch=40, staff=1),
        Note(t_ms=0, d_ms=500, pitch=80, staff=1),
    ]
    hands = assign_hands(notes, "pitch")
    assert {hands[id(n)] for n in notes} == {"L", "R"}


def test_resolve_hand_mode_auto_prefers_staff_when_any_note_is_explicit():
    explicit = Note(t_ms=0, d_ms=500, pitch=60, staff=1, staff_is_explicit=True)
    defaulted = Note(t_ms=500, d_ms=500, pitch=61, staff=1, staff_is_explicit=False)
    assert resolve_hand_mode([explicit, defaulted], "auto") == "staff"
    assert resolve_hand_mode([defaulted], "auto") == "pitch"
    # Non-"auto" modes pass through untouched.
    assert resolve_hand_mode([defaulted], "staff") == "staff"
    assert resolve_hand_mode([defaulted], "nonsense") == "nonsense"


# ---------------------------------------------------------------------------
# "staff" mode is untouched: pinned, byte-identical regression.
# ---------------------------------------------------------------------------


# Captured by running musicxml_to_lightscore(grand-staff.musicxml, hand_mode="staff")
# on this repo's code (both A2's hand-assignment delegation and the Deliverable-2
# cost-model change applied -- this specific fixture's chords are unaffected by the
# new finger_spacing_mismatch term, verified by diffing against a run with that
# weight forced to 0.0). This is the "before" snapshot the test pins.
_EXPECTED_STAFF_MODE_NOTES = [
    {"t": 0, "d": 1000, "p": 48, "h": "L", "f": 5, "stf": 2},
    {"t": 0, "d": 1000, "p": 55, "h": "L", "f": 1, "stf": 2},
    {"t": 0, "d": 500, "p": 60, "h": "R", "f": 1, "stf": 1},
    {"t": 500, "d": 500, "p": 62, "h": "R", "f": 2, "stf": 1},
    {"t": 1000, "d": 1000, "p": 64, "h": "R", "f": 3, "stf": 1},
    {"t": 2000, "d": 2000, "p": 41, "h": "L", "f": 5, "stf": 2},
    {"t": 2000, "d": 2000, "p": 48, "h": "L", "f": 2, "stf": 2},
    {"t": 2000, "d": 500, "p": 66, "h": "R", "f": 4, "stf": 1},
    {"t": 3000, "d": 2000, "p": 67, "h": "R", "f": 3, "stf": 1},
    {"t": 4000, "d": 2000, "p": 43, "h": "L", "f": 4, "stf": 2},
    {"t": 5000, "d": 1000, "p": 70, "h": "R", "f": 4, "stf": 1},
]


def test_staff_mode_output_is_byte_identical_to_before_a2():
    doc = musicxml_to_lightscore(_GRAND_STAFF, hand_mode="staff")
    assert doc["notes"] == _EXPECTED_STAFF_MODE_NOTES


# ---------------------------------------------------------------------------
# The defect this deliverable closes: no <staff> at all -> auto mode recovers
# the left hand from pitch instead of dumping everything on "R".
# ---------------------------------------------------------------------------


@pytest.fixture()
def staff_stripped_path(tmp_path):
    src = open(_GRAND_STAFF, encoding="utf-8").read()
    original_count = len(re.findall(r"<staff>\d+</staff>", src))
    stripped, n_removed = re.subn(r"<staff>\d+</staff>\s*\n?", "", src)
    print("staff-strip fixture: removed %d/%d <staff> elements" % (n_removed, original_count))
    assert original_count > 0, "fixture unexpectedly has no <staff> elements to strip"
    assert n_removed == original_count, (
        "regex removed %d of %d <staff> elements" % (n_removed, original_count)
    )
    assert "<staff>" not in stripped, "regex failed to strip all note-level <staff> elements"
    assert stripped.count("<staff-layout") == src.count("<staff-layout"), (
        "regex over-matched and removed <staff-layout>, not just <staff>"
    )
    out = tmp_path / "grand-staff-no-staff.musicxml"
    out.write_text(stripped, encoding="utf-8")
    return str(out)


def test_score_with_no_staff_has_staff_is_explicit_false(staff_stripped_path):
    sc = parse_musicxml(staff_stripped_path)
    assert len(sc.notes) == 11
    assert all(not n.staff_is_explicit for n in sc.notes)
    assert all(n.staff == 1 for n in sc.notes)


def test_auto_mode_would_be_all_right_hand_under_old_staff_default(staff_stripped_path):
    """Documents the exact defect: the OLD only-mode ("staff") on a staff-less
    source puts every note in the right hand, bass included. This is what
    hand_mode="auto" (the new default) exists to avoid -- see the next test."""
    doc = musicxml_to_lightscore(staff_stripped_path, hand_mode="staff")
    hands = {fn["h"] for fn in doc["notes"]}
    assert hands == {"R"}, "sanity check on the defect itself: expected an all-R result, got %r" % hands


def test_auto_mode_recovers_left_hand_from_staff_stripped_fixture(staff_stripped_path):
    real = musicxml_to_lightscore(_GRAND_STAFF, hand_mode="auto")
    auto = musicxml_to_lightscore(staff_stripped_path, hand_mode="auto")

    # The real grand-staff fixture's own LH notes (ground truth): everything staff==2
    # in the untouched source, i.e. every pitch < 60 here (see test_staff_and_hand_
    # orientation in test_musicxml.py, item 4, which pins this same fact).
    real_bass = [n for n in real["notes"] if n["p"] < 60]
    assert len(real_bass) == 5, "expected 5 ground-truth LH notes, found %d" % len(real_bass)

    auto_by_key = {(n["t"], n["p"]): n["h"] for n in auto["notes"]}
    bass_in_left = sum(1 for n in real_bass if auto_by_key[(n["t"], n["p"])] == "L")
    print("bass notes landing in L under auto mode: %d/%d" % (bass_in_left, len(real_bass)))
    assert bass_in_left >= 4, (
        "expected the great majority of the %d ground-truth bass notes to land in L "
        "under hand_mode='auto'; got %d" % (len(real_bass), bass_in_left)
    )
    assert bass_in_left > 0, "auto mode must not reproduce the old all-R defect"

    # Overall agreement with the real (staff-bearing) fixture's own hand assignment.
    real_by_key = {(n["t"], n["p"]): n["h"] for n in real["notes"]}
    total = len(real_by_key)
    matches = sum(1 for k, h in real_by_key.items() if auto_by_key.get(k) == h)
    print("auto-mode hand matches real grand-staff hand: %d/%d notes" % (matches, total))
    assert total == 11
    assert matches >= 9, "auto mode should recover most of the %d notes' hands, got %d" % (total, matches)

    # And the warning still fires, updated to describe what "auto" does about it.
    sc = parse_musicxml(staff_stripped_path)
    warning = next((w for w in sc.meta["warnings"] if "no <staff>" in w), None)
    assert warning is not None, "the no-<staff> warning must still fire"
    assert "auto" in warning, "the warning should say what the new default does about it"
