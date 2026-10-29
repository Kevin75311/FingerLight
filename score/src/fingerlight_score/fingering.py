"""Automatic piano fingering: a global dynamic program over the whole piece.

Ported from the *structure* of ``scoreforge/src/scoreforge/tab.py``'s ``assign_frets``
(tab.py:245) -- group notes into simultaneous chord events, enumerate a bounded set of
candidate voicings per event, then find the globally cheapest path through the events
by forward DP with a pairwise transition cost, and backtrack. NOT a code import: this
module is stdlib-only and must never import ``scoreforge`` (it pulls in
``guitarpro``/``basic_pitch`` and pins Python <3.12; the ambient interpreter here is
3.12.7).

The inversion vs. guitar TAB (closing Q-04, see docs/research/fingering-survey.md
sec.1's survey of jgollub1/guitar_dp): a guitar tab has no finger numbers, so
``assign_frets`` tracks (string, fret) and only a scalar hand-position proxy for
transitions. Piano fingering has real finger identity as its whole output (1=thumb..
5=little), so here finger identity IS the DP's state, and the transition cost can be
directly about which finger moves to which pitch -- the guitar_dp survey flagged this
"predict-then-penalize-deviation" idea as not portable to guitar but exactly the
right shape for piano; see ``_pairwise_transition`` below.

Design points (asked for explicitly, so recorded here rather than scattered in code):

1. **Event grouping** -- ``group_chord_events``, tolerance-based clustering by onset,
   identical in shape to ``tab.group_chord_events``.
2. **Candidate generation** -- ``_enumerate_candidates``. Hard-pruned: a chord's
   fingers are restricted to *monotonically increasing* finger number with
   increasing pitch (thumb-most digits on the lowest notes). This is not just a
   speed hack, it reflects how chords are actually played -- crossing fingers over
   each other to hold down a *simultaneous* chord is essentially never used. It
   turns what would be up to 5! = 120 permutations for a 5-note chord into
   C(5, k) <= 10 combinations (choose which k of the 5 fingers are used, in order).
   The DP itself still stays open to non-monotonic *sequences* across time (that's
   the whole point -- see point 3).
3. **Cost model**, five separable terms, all in ``COST_WEIGHTS`` so tuning is one
   edit:
     - ``stretch_per_semitone`` + ``comfort_span`` -- for any two fingers of the same
       hand holding two pitches at once (intra-chord) *or* moving from one event to
       the next (inter-event), the semitone distance is compared against that finger
       pair's comfortable span (a little table, thumb-pinky widest, adjacent fingers
       narrowest); only the excess over comfort is penalized.
     - ``order_violation`` -- intra-chord only: a higher finger number on a lower
       simultaneous pitch (crossing within one held chord). Always 0 under the
       current monotonic-only candidate generation (point 2); kept as a real,
       evaluated term rather than an assumed invariant so a future candidate
       generator that *does* emit crossed chord shapes (e.g. a very wide spread
       chord) is charged for it rather than silently allowed.
     - ``transition_cross_base`` + ``transition_cross_per_semitone`` -- between two
       events (never within one), a finger-number/pitch order reversal (thumb going
       under, or a finger crossing over the thumb) cost. This is what makes 1-2-3-1-
       2-3-4-5 beat running out of fingers: it is a *flat, small* cost plus a term
       proportional to the semitone gap actually being crossed (crossing under a
       half-step is cheaper than under a whole step -- the thumb has less lateral
       distance to cover), so among several places a scale *could* thumb-under, the
       DP prefers the narrowest gap.
     - ``finger_jump_per_number`` -- a small cost on *any* finger change (crossing or
       not), proportional to how many finger-numbers apart the two fingers are. This
       is what makes consecutive scale steps prefer adjacent fingers (1-2-3-4-5)
       over skipping (1-3-5-...): skipping is not otherwise penalized because a small
       pitch step never exceeds any finger pair's comfort span.
     - ``black_key_thumb`` -- a flat cost whenever finger 1 lands on a black key.
     - ``repeat_finger_moved`` -- reusing the same finger for two different pitches
       in successive events. Free when the pitch does not change (a genuine repeat),
       costed otherwise.
     - ``finger_spacing_mismatch`` -- intra-chord only (closing the cost-model
       tie-break defect found on a root C major triad, C4/E4/G4, which used to come
       out 1-2-4: both 1-2-4 and 1-3-5 cost exactly 0.0 under every term above, so
       enumeration order picked 1-2-4). For each pair of *adjacent* fingers within a
       chord's assignment, compares the actual pitch gap between them to the "evenly
       spread" expectation (the chord's total pitch span divided proportionally by
       the total finger span), and penalizes the deviation. This is what makes 1-3-5
       beat 1-2-4 on a root triad: 1-3-5 spreads its fingers roughly in proportion to
       the (roughly even, 4-then-3-semitone) pitch gaps, while 1-2-4 bunches two
       fingers together on the smaller gap and then jumps -- the "natural hand
       shape" a pianist actually uses on an open triad, vs. a cramped one that
       happens to also fit under the comfort-span limits.
   Tuned so that, brute-force-verified against all 5**8 fingerings, the *unique*
   global optimum for an ascending C-major one-octave scale is 1-2-3-1-2-3-4-5 (the
   textbook fingering) and for the descending scale is 5-4-3-2-1-3-2-1 (also
   textbook) -- see score/tests/test_fingering.py. Both scales are monophonic (one
   note per chord event), so ``finger_spacing_mismatch`` -- being intra-chord only --
   is identically 0.0 throughout and cannot move either result.
4. **Hand assignment** -- moved to ``hands.py`` (closing Q-09). Three modes,
   selected by ``hand_mode``: ``"staff"`` (staff 1 -> "R", anything else -> "L";
   unchanged, still the only mode that reads ``note.staff``), ``"pitch"`` (splits by
   a distribution-derived pitch threshold, time-smoothed so a hand doesn't flip
   mid-phrase), and ``"auto"`` (the default: ``"staff"`` when the source carried
   real staff info, ``"pitch"`` when it carried none -- see ``hands.py``'s module
   docstring for the full design). Two hands never share fingers, so their DPs stay
   fully independent regardless of mode -- each hand's note sequence, once hands are
   decided, is solved as its own smaller DP exactly as before.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .hands import assign_hands, resolve_hand_mode
from .notes import FingeredNote, Note

DEFAULT_ONSET_TOLERANCE_MS = 30

# Comfortable stretch, in semitones, per unordered finger pair (1=thumb..5=little).
# Approximate average-adult-hand figures; deliberately soft (a cost, not a hard
# reject) so an unusually wide transcribed chord still gets *some* fingering.
_COMFORT_SPAN: Dict[Tuple[int, int], int] = {
    (1, 2): 4,
    (1, 3): 6,
    (1, 4): 8,
    (1, 5): 10,
    (2, 3): 3,
    (2, 4): 5,
    (2, 5): 7,
    (3, 4): 3,
    (3, 5): 5,
    (4, 5): 3,
}

# Single source of truth for every tunable weight -- see the module docstring,
# point 3, for what each one does and why. Values are brute-force-verified (against
# all 5**8 fingerings of the acceptance scale) to make 1-2-3-1-2-3-4-5 the *unique*
# cheapest ascending fingering and 5-4-3-2-1-3-2-1 the unique cheapest descending one.
COST_WEIGHTS: Dict[str, object] = {
    "comfort_span": dict(_COMFORT_SPAN),
    "stretch_per_semitone": 40.0,
    "order_violation": 8.0,
    "transition_cross_base": 8.0,
    "transition_cross_per_semitone": 4.0,
    "finger_jump_per_number": 1.0,
    "black_key_thumb": 20.0,
    "repeat_finger_moved": 30.0,
    "finger_spacing_mismatch": 3.0,
}


def _comfort_span(f1: int, f2: int) -> int:
    if f1 == f2:
        return 0
    lo, hi = (f1, f2) if f1 < f2 else (f2, f1)
    return COST_WEIGHTS["comfort_span"][(lo, hi)]  # type: ignore[index]


def _is_black_key(pitch: int) -> bool:
    return pitch % 12 in (1, 3, 6, 8, 10)


# ---------------------------------------------------------------------------
# Chord-event grouping (mirrors tab.group_chord_events; ms instead of seconds)
# ---------------------------------------------------------------------------


def group_chord_events(
    notes: Sequence[Note], tolerance_ms: int = DEFAULT_ONSET_TOLERANCE_MS
) -> List[List[Note]]:
    """Cluster notes into "chord events": groups whose onsets fall within
    ``tolerance_ms`` of the group's anchor (first) note. Same greedy, non-optimal-
    but-good-enough clustering as ``scoreforge.tab.group_chord_events``."""
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: n.t_ms)
    events: List[List[Note]] = [[ordered[0]]]
    for note in ordered[1:]:
        anchor_t = events[-1][0].t_ms
        if note.t_ms - anchor_t <= tolerance_ms:
            events[-1].append(note)
        else:
            events.append([note])
    return events


# ---------------------------------------------------------------------------
# Hand assignment -- the one deliberately-simple seam (Q-09)
# ---------------------------------------------------------------------------


# Hand assignment itself now lives in hands.py (closing Q-09: "staff", "pitch" and
# "auto" modes) -- assign_fingering below calls resolve_hand_mode/assign_hands once
# per call and reuses the resulting per-note mapping instead of re-deciding per note.


# ---------------------------------------------------------------------------
# Candidate generation (per hand, per chord event)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Candidate:
    """One playable finger assignment for one hand's notes in one chord event."""

    assignment: Tuple[Tuple[Note, int], ...]  # (note, finger), pitch-ascending
    cost: float


def _axis(pitch: int, hand: str) -> int:
    """Pitch on a *hand-relative* axis, so one rule describes both hands.

    The left hand is the mirror image of the right: its thumb sits at the **high**
    end of what it is holding and the little finger at the low end, the opposite of
    the right hand.  Every ordering rule below ("finger number increases along the
    hand") is therefore stated on this axis rather than on raw pitch -- for "R" it is
    pitch itself, for "L" it is negated.

    Distances (`span`, used for stretch) are absolute and unaffected by the mirror;
    only *direction* comparisons -- ordering and crossing -- need it.
    """
    return pitch if hand == "R" else -pitch


def _intra_event_cost(assignment: Tuple[Tuple[Note, int], ...], hand: str) -> float:
    cost = 0.0
    for note, finger in assignment:
        if finger == 1 and _is_black_key(note.pitch):
            cost += COST_WEIGHTS["black_key_thumb"]
    for i in range(len(assignment)):
        note_i, f_i = assignment[i]
        for j in range(i + 1, len(assignment)):
            note_j, f_j = assignment[j]
            span = abs(note_j.pitch - note_i.pitch)
            excess = span - _comfort_span(f_i, f_j)
            if excess > 0:
                cost += excess * COST_WEIGHTS["stretch_per_semitone"]
            axis_delta = _axis(note_j.pitch, hand) - _axis(note_i.pitch, hand)
            if axis_delta * (f_j - f_i) < 0:
                cost += COST_WEIGHTS["order_violation"]

    # finger_spacing_mismatch: intra-chord, adjacent-finger-pairs only (see module
    # docstring point 3). Compares each adjacent pair's actual pitch gap against the
    # "evenly spread across the chord" expectation, so bunching two fingers on a
    # small gap and then jumping is penalized even when every pairwise stretch is
    # within its comfort span. `assignment` is already ordered along the hand axis
    # with strictly increasing finger numbers (see _enumerate_candidates), so
    # consecutive entries are exactly the adjacent-finger pairs and the total finger
    # span (last - first) is always >= 1 whenever there are >= 2 notes.
    if len(assignment) >= 2:
        first_note, first_finger = assignment[0]
        last_note, last_finger = assignment[-1]
        total_finger_span = last_finger - first_finger
        total_pitch_span = abs(last_note.pitch - first_note.pitch)
        avg_semitones_per_finger = total_pitch_span / total_finger_span
        for i in range(len(assignment) - 1):
            note_i, f_i = assignment[i]
            note_j, f_j = assignment[i + 1]
            finger_gap = f_j - f_i
            pitch_gap = abs(note_j.pitch - note_i.pitch)
            expected_gap = avg_semitones_per_finger * finger_gap
            cost += abs(pitch_gap - expected_gap) * COST_WEIGHTS["finger_spacing_mismatch"]

    return cost


def _enumerate_candidates(event_notes: Sequence[Note], hand: str) -> List[_Candidate]:
    """Candidate finger assignments for one hand's notes within one chord event.

    Notes are sorted along the **hand-relative axis** (see :func:`_axis`); candidates
    are every way to choose ``k`` of the 5 fingers, in increasing order, mapped onto
    that ordering (see module docstring point 2 for why this pruning is not just a
    speed hack). At most C(5, k) <= 10 candidates.

    Sorting on the axis rather than on raw pitch is what makes the left hand come out
    mirrored: for "R" the thumb lands on the lowest note of the chord, for "L" on the
    **highest**, which is how the left hand is actually shaped.

    Chords with more than 5 simultaneous notes on one hand drop the excess from the
    little-finger end of the axis (documented simplification -- a 6+-note single-hand
    chord is already outside normal piano technique)."""
    if not event_notes:
        return []
    sorted_notes = sorted(event_notes, key=lambda n: _axis(n.pitch, hand))
    if len(sorted_notes) > 5:
        sorted_notes = sorted_notes[:5]
    k = len(sorted_notes)

    candidates = []
    for combo in itertools.combinations(range(1, 6), k):
        assignment = tuple(zip(sorted_notes, combo))
        candidates.append(
            _Candidate(assignment=assignment, cost=_intra_event_cost(assignment, hand))
        )
    candidates.sort(key=lambda c: c.cost)
    return candidates


# ---------------------------------------------------------------------------
# Transition cost (between two consecutive events of the same hand)
# ---------------------------------------------------------------------------


def _pairwise_transition(f1: int, p1: int, f2: int, p2: int, hand: str) -> float:
    """Cost of moving one finger from (f1, p1) to (f2, p2), f1 != f2.

    The crossing test is on the hand-relative axis (:func:`_axis`): a thumb-under is
    "finger number moved opposite to the hand's direction", which for the left hand
    means the opposite direction in raw pitch.  Getting this wrong does not produce
    an error -- it produces a left hand fingered as though it were a right hand.
    """
    span = abs(p2 - p1)
    excess = span - _comfort_span(f1, f2)
    cost = excess * COST_WEIGHTS["stretch_per_semitone"] if excess > 0 else 0.0
    if (_axis(p2, hand) - _axis(p1, hand)) * (f2 - f1) < 0:
        cost += COST_WEIGHTS["transition_cross_base"] + COST_WEIGHTS["transition_cross_per_semitone"] * span
    cost += COST_WEIGHTS["finger_jump_per_number"] * abs(f2 - f1)
    return cost


def _transition_cost(
    prev_assignment: Tuple[Tuple[Note, int], ...],
    next_assignment: Tuple[Tuple[Note, int], ...],
    hand: str,
) -> float:
    """Cost of moving from one event's finger assignment to the next event's.

    Each finger used in ``next_assignment`` is charged independently: if the same
    finger was already down in ``prev_assignment``, that is a finger-reuse (free on
    a repeated pitch, ``repeat_finger_moved`` otherwise). If it's a finger not used
    in the previous event, its cost is the cheapest ``_pairwise_transition`` against
    any finger that *was* down previously -- i.e. "explain this finger's move
    relative to whichever previous finger makes it least awkward." For the
    monophonic case (one note per event, the common case and the one the
    acceptance scale test exercises) this reduces to exactly one term."""
    prev_by_finger: Dict[int, int] = {f: n.pitch for n, f in prev_assignment}
    if not prev_by_finger:
        return 0.0

    total = 0.0
    for note, f2 in next_assignment:
        p2 = note.pitch
        if f2 in prev_by_finger:
            p1 = prev_by_finger[f2]
            if p1 != p2:
                total += COST_WEIGHTS["repeat_finger_moved"]
            continue
        best = min(
            _pairwise_transition(f1, p1, f2, p2, hand)
            for f1, p1 in prev_by_finger.items()
        )
        total += best
    return total


# ---------------------------------------------------------------------------
# Per-hand DP (forward DP + backtrack, same shape as tab.assign_frets)
# ---------------------------------------------------------------------------


def _assign_fingering_one_hand(hand_events: Sequence[Sequence[Note]], hand: str) -> List[FingeredNote]:
    event_candidates = [_enumerate_candidates(ev, hand) for ev in hand_events]
    event_candidates = [c for c in event_candidates if c]
    if not event_candidates:
        return []

    # states[i][j] = (total cost of the cheapest path ending at candidate j of
    # event i, backpointer into states[i-1]).
    states: List[List[Tuple[float, int]]] = [[(cand.cost, -1) for cand in event_candidates[0]]]

    for idx in range(1, len(event_candidates)):
        prev_states = states[-1]
        prev_candidates = event_candidates[idx - 1]
        current: List[Tuple[float, int]] = []
        for cand in event_candidates[idx]:
            best_total = float("inf")
            best_prev = -1
            for p_idx, (p_cost, _) in enumerate(prev_states):
                trans = _transition_cost(
                    prev_candidates[p_idx].assignment, cand.assignment, hand
                )
                total = p_cost + trans
                if total < best_total:
                    best_total = total
                    best_prev = p_idx
            current.append((best_total + cand.cost, best_prev))
        states.append(current)

    chosen = [0] * len(event_candidates)
    last_idx = min(range(len(states[-1])), key=lambda i: states[-1][i][0])
    for idx in range(len(event_candidates) - 1, -1, -1):
        chosen[idx] = last_idx
        last_idx = states[idx][last_idx][1]

    result: List[FingeredNote] = []
    for idx, cands in enumerate(event_candidates):
        for note, finger in cands[chosen[idx]].assignment:
            result.append(FingeredNote.from_note(note, hand, finger))
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def assign_fingering(notes: Sequence[Note], hand_mode: str = "auto") -> List[FingeredNote]:
    """Pure function: notes in, fingered notes out.

    Groups ``notes`` into chord events (see :func:`group_chord_events`), assigns
    each note a hand (see ``hands.py``'s :func:`~fingerlight_score.hands.resolve_hand_mode`
    / :func:`~fingerlight_score.hands.assign_hands`, point 4 above), then -- since the
    two hands never share fingers -- solves each hand's note sequence as its own
    independent global DP (see :func:`_assign_fingering_one_hand`) over that hand's
    per-event candidate voicings (:func:`_enumerate_candidates`), with the pairwise
    transition cost of :func:`_transition_cost` charged between consecutive events.
    Guarantees: every input note gets exactly one hand and one finger 1-5; within
    any one chord event, no two same-hand notes share a finger.

    ``hand_mode="auto"`` (the default) resolves to ``"staff"`` when the source
    carried real staff info and ``"pitch"`` otherwise -- see ``hands.py``. Passing
    ``"staff"`` explicitly always behaves exactly as before A2 (byte-identical
    output, pinned by tests).
    """
    if not notes:
        return []

    resolved_mode = resolve_hand_mode(notes, hand_mode)
    hand_of = assign_hands(notes, resolved_mode)
    events = group_chord_events(notes)

    result: List[FingeredNote] = []
    for hand in ("R", "L"):
        hand_events = [[n for n in ev if hand_of[id(n)] == hand] for ev in events]
        hand_events = [ev for ev in hand_events if ev]
        if not hand_events:
            continue
        result.extend(_assign_fingering_one_hand(hand_events, hand))

    result.sort(key=lambda fn: (fn.t_ms, fn.pitch))
    return result
