"""Unit tests for the pure chord logic in scoreforge.chord.

These use only synthetic ChordSegment/label data -- no madmom/audio I/O, since madmom
is an optional dependency (see chord.py's module docstring for why).
Run with:  python -m unittest tests.test_chord -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scoreforge.chord import (
    ChordSegment,
    chords_per_measure,
    expected_pitch_classes,
    flag_chord_mismatches,
    parse_chord_label,
    suggest_capo,
)


class TestParseChordLabel(unittest.TestCase):
    def test_major(self):
        self.assertEqual(parse_chord_label("D#:maj"), (3, "maj"))

    def test_minor(self):
        self.assertEqual(parse_chord_label("C:min"), (0, "min"))

    def test_no_chord(self):
        self.assertIsNone(parse_chord_label("N"))

    def test_unrecognized_root(self):
        self.assertIsNone(parse_chord_label("H:maj"))


class TestExpectedPitchClasses(unittest.TestCase):
    def test_c_major_triad(self):
        # C=0, E=4, G=7
        self.assertEqual(expected_pitch_classes(0, "maj"), {0, 4, 7})

    def test_a_minor_triad(self):
        # A=9, C=0, E=4
        self.assertEqual(expected_pitch_classes(9, "min"), {9, 0, 4})

    def test_unknown_quality_falls_back_to_major(self):
        self.assertEqual(expected_pitch_classes(0, "sus4"), {0, 4, 7})


class TestChordsPerMeasure(unittest.TestCase):
    def test_single_chord_spanning_one_measure(self):
        # tempo=120 -> 2 seconds/measure (4 beats * 60/120)
        segments = [ChordSegment(start=0.0, end=2.0, label="C:maj")]
        result = chords_per_measure(segments, tempo=120.0, n_measures=1)
        self.assertEqual(result, ["C:maj"])

    def test_majority_overlap_wins(self):
        # Measure 0 spans [0, 2). A:maj covers [0, 1.5) = 1.5s, D:maj covers [1.5, 2) = 0.5s.
        segments = [
            ChordSegment(start=0.0, end=1.5, label="A:maj"),
            ChordSegment(start=1.5, end=3.0, label="D:maj"),
        ]
        result = chords_per_measure(segments, tempo=120.0, n_measures=1)
        self.assertEqual(result, ["A:maj"])

    def test_no_segments_returns_all_no_chord(self):
        result = chords_per_measure([], tempo=120.0, n_measures=3)
        self.assertEqual(result, ["N", "N", "N"])

    def test_zero_measures_returns_empty(self):
        self.assertEqual(chords_per_measure([], tempo=120.0, n_measures=0), [])

    def test_gap_with_no_coverage_is_no_chord(self):
        segments = [ChordSegment(start=10.0, end=12.0, label="G:maj")]
        result = chords_per_measure(segments, tempo=120.0, n_measures=1)  # measure 0 = [0,2)
        self.assertEqual(result, ["N"])


class TestFlagChordMismatches(unittest.TestCase):
    def test_matching_notes_not_flagged(self):
        # C major triad notes (C, E, G) against a detected C:maj chord.
        flags = flag_chord_mismatches(["C:maj"], [{0, 4, 7}])
        self.assertEqual(flags, [False])

    def test_unrelated_notes_flagged(self):
        # F#, G#, A# have zero overlap with a C major triad {0, 4, 7}.
        flags = flag_chord_mismatches(["C:maj"], [{6, 8, 10}])
        self.assertEqual(flags, [True])

    def test_no_chord_label_never_flagged(self):
        flags = flag_chord_mismatches(["N"], [{6, 8, 10}])
        self.assertEqual(flags, [False])

    def test_empty_pitch_classes_never_flagged(self):
        flags = flag_chord_mismatches(["C:maj"], [set()])
        self.assertEqual(flags, [False])


class TestSuggestCapo(unittest.TestCase):
    def test_open_chords_prefer_capo_zero(self):
        # E, A, D are all open shapes already -- capo 0 should win outright.
        result = suggest_capo(["E:maj", "A:maj", "D:maj"])
        self.assertEqual(result["capo"], 0)
        self.assertEqual(result["open_shape_count"], 3)

    def test_barre_chords_find_a_better_capo(self):
        # F, Bb, Eb are all barre shapes at capo 0, but transposing down by 1 fret
        # (capo 1) turns them into E, A, D -- all open shapes.
        result = suggest_capo(["F:maj", "A#:maj", "D#:maj"])
        self.assertEqual(result["capo"], 1)
        self.assertEqual(result["open_shape_count"], 3)
        self.assertEqual(result["shapes"], ["E", "A", "D"])

    def test_empty_input(self):
        result = suggest_capo([])
        self.assertEqual(result["capo"], 0)
        self.assertEqual(result["total"], 0)


if __name__ == "__main__":
    unittest.main()
