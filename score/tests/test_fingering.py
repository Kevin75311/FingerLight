import itertools
import json
from pathlib import Path

import pytest

from fingerlight_score.fingering import (
    COST_WEIGHTS,
    _intra_event_cost,
    _pairwise_transition,
    assign_fingering,
    group_chord_events,
)
from fingerlight_score.notes import Note

FIXTURES = Path(__file__).resolve().parents[2] / "lightscore" / "examples"


def _scale_notes(pitches, staff=1, step_ms=500, dur_ms=500):
    return [
        Note(t_ms=i * step_ms, d_ms=dur_ms, pitch=p, staff=staff)
        for i, p in enumerate(pitches)
    ]


# ---------------------------------------------------------------------------
# The headline test: does the DP discover the thumb-under on its own?
# ---------------------------------------------------------------------------


def test_c_major_scale_ascending_discovers_thumb_under():
    pitches = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = _scale_notes(pitches)

    fingered = assign_fingering(notes, hand_mode="staff")

    assert [fn.pitch for fn in fingered] == pitches
    assert [fn.hand for fn in fingered] == ["R"] * 8
    assert [fn.finger for fn in fingered] == [1, 2, 3, 1, 2, 3, 4, 5]


def test_c_major_scale_descending_is_sensible():
    pitches = [72, 71, 69, 67, 65, 64, 62, 60]
    notes = _scale_notes(pitches)

    fingered = assign_fingering(notes, hand_mode="staff")
    fingers = [fn.finger for fn in fingered]

    # Textbook descending RH fingering. Brute-force-verified (see fingering.py's
    # module docstring / COST_WEIGHTS) to be the *unique* cheapest path under
    # COST_WEIGHTS for this exact scale.
    assert fingers == [5, 4, 3, 2, 1, 3, 2, 1]


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def test_group_chord_events_clusters_by_onset_tolerance():
    notes = [
        Note(t_ms=0, d_ms=500, pitch=60, staff=1),
        Note(t_ms=10, d_ms=500, pitch=64, staff=1),  # within tolerance of anchor
        Note(t_ms=500, d_ms=500, pitch=67, staff=1),
    ]
    events = group_chord_events(notes, tolerance_ms=30)
    assert len(events) == 2
    assert {n.pitch for n in events[0]} == {60, 64}
    assert [n.pitch for n in events[1]] == [67]


def test_hand_mode_staff_maps_staff_to_hand():
    notes = [
        Note(t_ms=0, d_ms=500, pitch=60, staff=1),
        Note(t_ms=0, d_ms=500, pitch=48, staff=2),
    ]
    fingered = assign_fingering(notes, hand_mode="staff")
    hands = {fn.pitch: fn.hand for fn in fingered}
    assert hands[60] == "R"
    assert hands[48] == "L"


def test_unsupported_hand_mode_raises():
    notes = [Note(t_ms=0, d_ms=500, pitch=60, staff=1)]
    with pytest.raises(NotImplementedError):
        assign_fingering(notes, hand_mode="split-by-pitch")


def test_empty_input_returns_empty():
    assert assign_fingering([]) == []


def test_chord_notes_on_one_hand_get_distinct_fingers():
    # A wide-ish RH chord: C4, E4, G4, C5 at once.
    notes = [
        Note(t_ms=0, d_ms=1000, pitch=60, staff=1),
        Note(t_ms=0, d_ms=1000, pitch=64, staff=1),
        Note(t_ms=0, d_ms=1000, pitch=67, staff=1),
        Note(t_ms=0, d_ms=1000, pitch=72, staff=1),
    ]
    fingered = assign_fingering(notes, hand_mode="staff")
    fingers = [fn.finger for fn in fingered]
    assert len(set(fingers)) == 4
    assert all(1 <= f <= 5 for f in fingers)
    # monotonic: lower pitch never gets a strictly higher finger than a
    # simultaneously-held higher pitch.
    by_pitch = sorted(fingered, key=lambda fn: fn.pitch)
    assert [fn.finger for fn in by_pitch] == sorted(fn.finger for fn in by_pitch)


# ---------------------------------------------------------------------------
# Two-hand fixture: every note gets *some* legal finger, both hands populated.
# Not expected to match the fixture's own hand-picked fingering exactly.
# ---------------------------------------------------------------------------


def test_two_hand_chords_fixture_every_note_gets_a_legal_finger():
    data = json.loads((FIXTURES / "two-hand-chords.lightscore.json").read_text(encoding="utf-8"))
    notes = [
        Note(t_ms=n["t"], d_ms=n["d"], pitch=n["p"], staff=n["stf"], velocity=n.get("v"))
        for n in data["notes"]
    ]

    fingered = assign_fingering(notes, hand_mode="staff")

    assert len(fingered) == len(notes)
    assert all(1 <= fn.finger <= 5 for fn in fingered)
    hands_present = {fn.hand for fn in fingered}
    assert hands_present == {"L", "R"}

    # No two same-hand notes in the same chord event share a finger.
    by_time_hand: dict = {}
    for fn in fingered:
        by_time_hand.setdefault((fn.t_ms, fn.hand), []).append(fn.finger)
    for key, fingers in by_time_hand.items():
        assert len(fingers) == len(set(fingers)), f"duplicate finger in {key}: {fingers}"


# ---------------------------------------------------------------------------
# Deliverable 2: cost-model tie-break (finger_spacing_mismatch)
# ---------------------------------------------------------------------------


def test_root_triad_prefers_1_3_5_over_1_2_4():
    """A root C major triad (C4 E4 G4, one hand, played together) used to come out
    1-2-4: both 1-2-4 and 1-3-5 cost exactly 0.0 under the five original terms (no
    pairwise stretch exceeds its comfort span), so enumeration order picked 1-2-4.
    finger_spacing_mismatch breaks that tie towards the natural, evenly-spread
    1-3-5 a pianist actually uses."""
    notes = [Note(t_ms=0, d_ms=1000, pitch=p, staff=1) for p in (60, 64, 67)]
    fingered = assign_fingering(notes, hand_mode="staff")
    by_pitch = {fn.pitch: fn.finger for fn in fingered}
    assert by_pitch == {60: 1, 64: 3, 67: 5}, (
        "root C major triad should finger 1-3-5, got %r" % by_pitch
    )


def _brute_force_optimum(pitches, hand="R"):
    """Exhaustively try every one of 5**len(pitches) finger assignments for a
    monophonic one-hand sequence (no candidate-generation pruning at all -- this is
    independent of, and does not call, the DP), using the exact same per-note and
    per-transition cost functions the DP uses. Returns (best_cost, [winning
    assignments]) so uniqueness can be checked directly."""
    notes = [Note(t_ms=i * 500, d_ms=500, pitch=p, staff=1) for i, p in enumerate(pitches)]
    n = len(notes)
    best_cost = None
    winners = []
    for combo in itertools.product(range(1, 6), repeat=n):
        cost = sum(_intra_event_cost(((note, finger),), hand) for note, finger in zip(notes, combo))
        for i in range(1, n):
            cost += _pairwise_transition(combo[i - 1], notes[i - 1].pitch, combo[i], notes[i].pitch, hand)
        if best_cost is None or cost < best_cost - 1e-9:
            best_cost, winners = cost, [combo]
        elif abs(cost - best_cost) < 1e-9:
            winners.append(combo)
    return best_cost, winners


def test_brute_force_scales_are_still_uniquely_optimal_after_the_new_cost_term():
    """Re-run of the module docstring's brute-force claim (point 3) after adding
    finger_spacing_mismatch. Both acceptance scales are monophonic (one note per
    chord event), so the new term -- intra-chord, adjacent-finger-pair only -- is
    identically 0.0 for every candidate here and cannot move either optimum; this
    test proves that by exhaustive enumeration rather than by argument."""
    ascending = [60, 62, 64, 65, 67, 69, 71, 72]
    descending = [72, 71, 69, 67, 65, 64, 62, 60]

    asc_cost, asc_winners = _brute_force_optimum(ascending)
    assert len(asc_winners) == 1, "ascending optimum is no longer unique: %r" % (asc_winners,)
    assert asc_winners[0] == (1, 2, 3, 1, 2, 3, 4, 5)

    desc_cost, desc_winners = _brute_force_optimum(descending)
    assert len(desc_winners) == 1, "descending optimum is no longer unique: %r" % (desc_winners,)
    assert desc_winners[0] == (5, 4, 3, 2, 1, 3, 2, 1)

    print(
        "brute force (5**8=%d assignments each): ascending cost=%r unique=%s; "
        "descending cost=%r unique=%s"
        % (5 ** 8, asc_cost, len(asc_winners) == 1, desc_cost, len(desc_winners) == 1)
    )
