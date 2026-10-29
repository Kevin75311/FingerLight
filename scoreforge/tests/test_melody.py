"""Unit tests for the pure vocal-melody reduction logic in melody.py.

Synthetic Note objects only -- no MIDI/audio I/O, per the project's testability
split. Run with:  python -m unittest tests.test_melody -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scoreforge.melody import reduce_to_melody
from scoreforge.tab import Note


class TestReduceToMelody(unittest.TestCase):
    def test_octave_ghost_is_removed(self):
        # A short harmonic ghost an octave above the sung note -- classic Basic
        # Pitch artifact on vocals. The longer (sung) note must win.
        notes = [
            Note(pitch=60, start=0.0, end=1.0, velocity=80),
            Note(pitch=72, start=0.0, end=0.3, velocity=60),
        ]
        melody = reduce_to_melody(notes)
        self.assertEqual([n.pitch for n in melody], [60])

    def test_boundary_smear_is_trimmed_not_dropped(self):
        # Consecutive sung notes overlapping slightly at the transition: both
        # survive, the earlier one trimmed so the line stays monophonic.
        notes = [
            Note(pitch=60, start=0.0, end=0.5),
            Note(pitch=62, start=0.45, end=1.0),
        ]
        melody = reduce_to_melody(notes)
        self.assertEqual([n.pitch for n in melody], [60, 62])
        self.assertAlmostEqual(melody[0].end, 0.45)

    def test_out_of_range_pitches_dropped(self):
        notes = [
            Note(pitch=30, start=0.0, end=0.5),   # far below vocal range
            Note(pitch=60, start=1.0, end=1.5),   # in range
            Note(pitch=100, start=2.0, end=2.5),  # far above vocal range
        ]
        melody = reduce_to_melody(notes)
        self.assertEqual([n.pitch for n in melody], [60])

    def test_short_blips_dropped(self):
        notes = [
            Note(pitch=60, start=0.0, end=0.05),  # below min duration
            Note(pitch=62, start=1.0, end=1.5),
        ]
        melody = reduce_to_melody(notes)
        self.assertEqual([n.pitch for n in melody], [62])

    def test_result_is_always_monophonic(self):
        # A messy overlapping cluster -- whatever survives, no two notes may sound
        # at the same time.
        notes = [
            Note(pitch=60, start=0.0, end=0.8),
            Note(pitch=64, start=0.2, end=1.2),
            Note(pitch=67, start=0.7, end=1.5),
            Note(pitch=65, start=1.4, end=2.0),
            Note(pitch=72, start=1.45, end=1.6),
        ]
        melody = reduce_to_melody(notes)
        self.assertGreater(len(melody), 0)
        for earlier, later in zip(melody, melody[1:]):
            self.assertLessEqual(earlier.end, later.start + 1e-9)

    def test_non_overlapping_line_passes_through(self):
        notes = [
            Note(pitch=60, start=0.0, end=0.5),
            Note(pitch=62, start=0.6, end=1.0),
            Note(pitch=64, start=1.1, end=1.6),
        ]
        melody = reduce_to_melody(notes)
        self.assertEqual([n.pitch for n in melody], [60, 62, 64])

    def test_empty_input(self):
        self.assertEqual(reduce_to_melody([]), [])


if __name__ == "__main__":
    unittest.main()
