"""Minimal note model for the fingering DP.

Deliberately not the same shape as ``scoreforge.tab.Note`` (seconds, no staff, no
finger): this module is never allowed to import scoreforge (it depends on
``guitarpro``/``basic_pitch`` and pins Python <3.12; the ambient interpreter here is
3.12.7). ``t_ms``/``d_ms`` in integer milliseconds matches lightscore's native units
directly (see ``lightscore/schema/lightscore-v1.schema.json``), so ``emit.py`` never
needs a unit conversion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Note:
    """One note to be fingered.

    ``staff`` is whatever the source (MIDI track, MusicXML part, ...) called it --
    1 = top/treble, 2 = bottom/bass by convention, but this module does not
    interpret it beyond that until hand assignment (see ``fingering.py``,
    ``hand_mode="staff"``).

    ``staff_is_explicit`` records whether ``staff`` came from a real marking in the
    source, or was defaulted because the source had none (see ``musicxml.py``'s
    ``_walk_part``, which is the only producer that actually knows the difference).
    Defaults to ``True`` so every pre-existing call site (tests, other producers)
    keeps its historical meaning -- "this note's staff value is real" -- without
    having to be touched. ``hands.py``'s ``"auto"`` mode is the sole consumer.
    """

    t_ms: int
    d_ms: int
    pitch: int  # MIDI pitch number
    staff: int
    velocity: Optional[int] = None
    staff_is_explicit: bool = True


@dataclass(frozen=True)
class FingeredNote:
    """A Note bound to a hand and finger.

    Flat (fields inlined, not ``note: Note``) so ``emit.py`` can read lightscore's
    t/d/p/h/f/stf/v fields straight off the same object without an extra hop.
    """

    t_ms: int
    d_ms: int
    pitch: int
    staff: int
    velocity: Optional[int]
    hand: str  # "L" or "R"
    finger: int  # 1 (thumb) .. 5 (little)

    @classmethod
    def from_note(cls, note: Note, hand: str, finger: int) -> "FingeredNote":
        return cls(
            t_ms=note.t_ms,
            d_ms=note.d_ms,
            pitch=note.pitch,
            staff=note.staff,
            velocity=note.velocity,
            hand=hand,
            finger=finger,
        )
