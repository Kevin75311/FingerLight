"""Hand assignment (project item A2, closing question Q-09).

Three modes, all reached through :func:`assign_hands` (``fingering.py`` delegates to
this module instead of implementing hand logic itself):

* ``"staff"`` -- the original, only-ever-implemented mode. ``note.staff == 2`` -> "L",
  anything else -> "R". Reads nothing but ``note.staff``. Kept byte-identical to the
  pre-A2 behaviour (see ``test_fingering.py`` / ``test_hand_orientation.py``, which
  pin it, and ``test_hands.py``'s explicit regression test against the grand-staff
  fixture).
* ``"pitch"`` -- for input with no reliable staff markup (a lead sheet, a bare
  export, or Audiveris OMR output -- see ``musicxml.py:361``'s warning). Splits notes
  into two hands by pitch. The split point is **not** a hardcoded middle C: it is
  chosen from the piece's own pitch distribution (:func:`_split_point`), because a
  piece transposed away from middle C, or one that simply favours one register, would
  otherwise get a lopsided or wrong split. A hard split on that point alone is not
  enough on its own -- a hand that transiently wanders across the boundary (a left
  hand walking up, a right hand reaching down) must not flip note-by-note -- so the
  raw per-note guess is smoothed over time by a small 2-state (L/R) Viterbi DP
  (:func:`_smooth_hand_sequence`) that penalizes changing hands between two
  *different-onset* notes but charges nothing between notes that sound at the same
  onset (so a genuine two-hand chord that straddles the split is not penalized for
  "flipping" -- see the "same_time" branch). This is the same DP-with-transition-cost
  shape as ``fingering.py``'s ``_assign_fingering_one_hand``, just with two states
  instead of finger-candidates.
* ``"auto"`` (the new default, see :func:`resolve_hand_mode`) -- ``"staff"`` when the
  source carried at least one real ``<staff>`` element, ``"pitch"`` when it carried
  none at all. This is a whole-piece decision, not a per-note one: a score missing
  staff info on a handful of notes still has real information worth trusting more
  than a pitch split; a score with none at all has nothing to trust.

Design choice for ``Note.staff_is_explicit`` (see ``notes.py``): tracked as a real
field set by the producer, not inferred after the fact from e.g. ``note.staff == 1``.
Inference cannot be told apart from a genuine, correctly-notated staff-1-only passage
(that is a completely normal thing for a real piece to have); only the producer
(``musicxml.py``'s ``_walk_part``, which sees whether a ``<staff>`` element was
actually present for each ``<note>``) knows the difference honestly. Defaulting the
field to ``True`` means every existing call site (tests, any future producer that
doesn't set it) keeps today's implicit assumption -- "the staff value is real" --
without needing to change.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .notes import Note

# Emission cost (for the pitch-mode smoothing DP) when a note's assigned hand
# disagrees with its raw pitch-vs-split guess: a flat base plus a per-semitone term,
# so a note just barely on the "wrong" side of the split is cheap to keep in its
# smoothed hand, while a note deep in the other hand's register is expensive to.
_MISMATCH_BASE = 2.0
_MISMATCH_PER_SEMITONE = 1.0

# Cost of switching the smoothed hand between two *different-onset* notes. Must beat
# the mismatch cost of a single stray note (so one outlier doesn't cause a flip) but
# be affordable over a genuine multi-note phrase change (so a real register shift
# still gets recognized). Between same-onset notes this cost is waived entirely (see
# _smooth_hand_sequence) -- simultaneous notes in different hands are normal, not a
# "flip".
_SWITCH_COST = 9.0


def resolve_hand_mode(notes: Sequence[Note], hand_mode: str) -> str:
    """Resolve ``"auto"`` to a concrete mode; pass any other mode through unchanged.

    ``"auto"`` -> ``"staff"`` if any note carries real staff info
    (``staff_is_explicit``), else ``"pitch"``. Mirrors the exact condition
    ``musicxml.py``'s ``_walk_part`` already uses for its "no <staff> elements"
    warning (``saw_staff_element``), so the two stay in sync by construction.
    """
    if hand_mode != "auto":
        return hand_mode
    if any(getattr(n, "staff_is_explicit", True) for n in notes):
        return "staff"
    return "pitch"


def assign_hands(notes: Sequence[Note], hand_mode: str) -> Dict[int, str]:
    """Map every note (keyed by ``id(note)``, i.e. its position in the input, not its
    value -- two value-identical notes played at once must not collide) to "L"/"R".

    ``hand_mode`` here must already be resolved (see :func:`resolve_hand_mode`) --
    this function only knows ``"staff"`` and ``"pitch"``; anything else raises
    ``NotImplementedError``, same as the old ``fingering._hand_for`` did for any mode
    beyond "staff".
    """
    if hand_mode == "staff":
        return {id(n): ("L" if n.staff == 2 else "R") for n in notes}
    if hand_mode == "pitch":
        return _assign_hands_by_pitch(notes)
    raise NotImplementedError(
        f"hand_mode={hand_mode!r} is not implemented. Supported modes: 'staff' "
        "(staff 1 -> R, else -> L), 'pitch' (split by pitch distribution), 'auto' "
        "('staff' when the source carried real staff info, 'pitch' otherwise)."
    )


# ---------------------------------------------------------------------------
# "pitch" mode
# ---------------------------------------------------------------------------


def _split_point(notes: Sequence[Note]) -> int:
    """Pick a pitch split point from the piece's own distribution -- never a
    hardcoded middle C.

    Runs 1-D 2-means on the set of *distinct* pitches used (distinct, not
    frequency-weighted, so one heavily-repeated note can't drag the centroid
    towards it) and returns the midpoint between the two converged centroids.

    A single-widest-gap heuristic was tried first and rejected: on the grand-staff
    fixture the LH itself spans C3..G3 (41..55, a 14-semitone chord-internal gap of
    7 between 48 and 55) while the true LH/RH boundary gap is only 5 semitones
    (55..60) -- the widest *single* gap sits inside one hand's own range, not at the
    boundary between hands. 2-means instead asks "which 2-way split minimizes the
    spread within each side", which is a global property of the whole distribution
    rather than one local gap, and correctly finds the 55/60 boundary in that case.

    Degenerates gracefully: fewer than 2 distinct pitches returns the only pitch (or
    60 if there are none, an arbitrary but harmless default since there is nothing
    to split). A distribution with no real separation (e.g. a dense chromatic run)
    still converges to *some* 2-way split near the middle, rather than refusing.
    """
    pitches = sorted({n.pitch for n in notes})
    if len(pitches) < 2:
        return pitches[0] if pitches else 60
    lo, hi = pitches[0], pitches[-1]
    if lo == hi:
        return lo

    c1, c2 = float(lo), float(hi)
    for _ in range(50):
        mid = (c1 + c2) / 2
        left = [p for p in pitches if p <= mid]
        right = [p for p in pitches if p > mid]
        if not left or not right:
            break
        new_c1 = sum(left) / len(left)
        new_c2 = sum(right) / len(right)
        if abs(new_c1 - c1) < 1e-9 and abs(new_c2 - c2) < 1e-9:
            c1, c2 = new_c1, new_c2
            break
        c1, c2 = new_c1, new_c2
    return round((c1 + c2) / 2)


def _raw_side(pitch: int, split: int) -> str:
    return "R" if pitch >= split else "L"


def _emission_cost(note: Note, state: str, split: int) -> float:
    if _raw_side(note.pitch, split) == state:
        return 0.0
    return _MISMATCH_BASE + _MISMATCH_PER_SEMITONE * abs(note.pitch - split)


def _smooth_hand_sequence(ordered: Sequence[Note], split: int) -> List[str]:
    """2-state (L/R) Viterbi smoothing of the raw pitch-vs-split guess over time.

    ``ordered`` must already be sorted by ``(t_ms, pitch)``. Notes sharing an onset
    (``t_ms`` equal to the previous note's) are linked with zero transition cost in
    either direction -- they are simultaneous, not sequential, so "the hand changed"
    is meaningless between them (this is what lets a genuine two-hand chord straddle
    the split for free). Notes at different onsets pay ``_SWITCH_COST`` to change
    hand, which is what keeps a single transient outlier note from flipping the
    whole sequence (its mismatch cost, staying put, is cheaper than paying the
    switch cost twice to dip out and back) while still letting a sustained register
    change (many notes' worth of mismatch cost) win once it outweighs the switch.

    Known simplification: within a simultaneous group the notes are linearized in
    pitch order (from :func:`_assign_hands_by_pitch`'s sort key) and the transition
    charge to the *next* different-onset note is anchored on the group's last
    (highest-pitch) member rather than "whichever voice actually continues". This
    under- or over-charges that one transition in wide simultaneous chords; it does
    not affect the notes within the group, which are free to differ from each other.
    """
    n = len(ordered)
    if n == 0:
        return []
    states = ("L", "R")
    dp: List[Dict[str, float]] = [{s: _emission_cost(ordered[0], s, split) for s in states}]
    back: List[Dict[str, Optional[str]]] = [{s: None for s in states}]

    for i in range(1, n):
        same_time = ordered[i].t_ms == ordered[i - 1].t_ms
        cur: Dict[str, float] = {}
        bp: Dict[str, Optional[str]] = {}
        for s in states:
            best_cost: Optional[float] = None
            best_prev: Optional[str] = None
            for ps in states:
                trans = 0.0 if (same_time or ps == s) else _SWITCH_COST
                total = dp[i - 1][ps] + trans
                if best_cost is None or total < best_cost:
                    best_cost, best_prev = total, ps
            cur[s] = best_cost + _emission_cost(ordered[i], s, split)
            bp[s] = best_prev
        dp.append(cur)
        back.append(bp)

    last = min(states, key=lambda s: dp[-1][s])
    result: List[str] = [""] * n
    result[-1] = last
    for i in range(n - 1, 0, -1):
        last = back[i][last]
        result[i - 1] = last
    return result


def _assign_hands_by_pitch(notes: Sequence[Note]) -> Dict[int, str]:
    if not notes:
        return {}
    split = _split_point(notes)
    ordered = sorted(notes, key=lambda n: (n.t_ms, n.pitch))
    hands = _smooth_hand_sequence(ordered, split)
    return {id(n): h for n, h in zip(ordered, hands)}
