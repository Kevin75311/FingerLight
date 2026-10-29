"""Vocal melody extraction: reduce Basic Pitch's output on a vocals stem to a
monophonic lead line.

Basic Pitch is a polyphonic transcriber, so even on a (mostly monophonic) vocal
stem it emits overlapping artifacts: harmonic ghosts an octave up, sub-octave
shadows on strong low notes, and short blips at note transitions. A usable
"extra notes from the vocal" track needs exactly one note at a time -- the main
melody -- so this module reduces the polyphonic note list to a monophonic one.

Everything here is pure (no MIDI/audio I/O), mirroring tab.py's testability
split: construct ``tab.Note`` objects synthetically and unit-test freely.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from .tab import Note

# G2..C6 -- covers low baritone through high soprano. Notes outside this range on a
# vocals stem are nearly always transcription artifacts (instrument bleed, harmonic
# ghosts), not sung melody.
DEFAULT_MELODY_PITCH_RANGE: Tuple[int, int] = (43, 84)

# Sung notes shorter than this are usually onset-detector blips at note
# transitions, not intentional notes.
DEFAULT_MIN_NOTE_DURATION = 0.09  # seconds

# Two selected notes overlapping by more than this fraction of the shorter note
# are considered "the same moment" -- only the more salient one survives. Below
# it, the overlap is treated as a transcription smear at a note boundary and
# trimmed instead.
DEFAULT_OVERLAP_RATIO = 0.5


def _salience(note: Note) -> Tuple[float, int, int]:
    """Which of two simultaneous notes is more likely the actual melody.

    Duration dominates: harmonic ghosts and octave shadows are consistently
    shorter-lived than the sung note they shadow. Velocity breaks ties, then
    higher pitch (the melody is the top voice more often than not)."""
    return (note.end - note.start, note.velocity, note.pitch)


def reduce_to_melody(
    notes: Sequence[Note],
    pitch_range: Tuple[int, int] = DEFAULT_MELODY_PITCH_RANGE,
    min_duration: float = DEFAULT_MIN_NOTE_DURATION,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> List[Note]:
    """Pure function: polyphonic vocal transcription in, monophonic melody out.

    1. Drop notes outside ``pitch_range`` or shorter than ``min_duration``.
    2. Walk notes in onset order keeping a single melodic line:
       - a note that overlaps the current line note by more than
         ``overlap_ratio`` of the shorter of the two competes with it -- the
         more salient one (see :func:`_salience`) survives;
       - a smaller overlap is treated as a boundary smear: the earlier note is
         trimmed to end where the later one starts, and both survive.

    The result never has two notes sounding at once.
    """
    low, high = pitch_range
    candidates = [
        n
        for n in notes
        if low <= n.pitch <= high and (n.end - n.start) >= min_duration
    ]
    candidates.sort(key=lambda n: (n.start, -_salience(n)[0]))

    melody: List[Note] = []
    for note in candidates:
        if not melody:
            melody.append(note)
            continue

        current = melody[-1]
        overlap = min(current.end, note.end) - note.start
        if overlap <= 0:
            melody.append(note)
            continue

        shorter = min(current.end - current.start, note.end - note.start)
        if shorter > 0 and overlap / shorter > overlap_ratio:
            # Same moment -- keep the more salient of the two.
            if _salience(note) > _salience(current):
                melody[-1] = note
            continue

        # Boundary smear -- trim the earlier note so the line stays monophonic.
        trimmed_end = note.start
        if trimmed_end - current.start >= min_duration:
            melody[-1] = Note(
                pitch=current.pitch,
                start=current.start,
                end=trimmed_end,
                velocity=current.velocity,
            )
            melody.append(note)
        else:
            # Trimming would leave a sliver -- resolve by salience instead.
            if _salience(note) > _salience(current):
                melody[-1] = note

    return melody
