"""Unit tests for the pure fret-assignment logic in scoreforge.tab.

These use only synthetic Note objects -- no MIDI/audio I/O -- per the project spec.
Run with:  python -m unittest tests.test_tab -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scoreforge.tab import (
    STANDARD_TUNING,
    Note,
    assign_frets,
    correct_tempo_octave,
    group_chord_events,
    merge_repeated_notes,
    render_ascii_tab,
)


class TestGroupChordEvents(unittest.TestCase):
    def test_simultaneous_notes_form_one_event(self):
        notes = [
            Note(pitch=40, start=0.0, end=1.0),
            Note(pitch=45, start=0.01, end=1.0),
            Note(pitch=50, start=0.02, end=1.0),
        ]
        events = group_chord_events(notes, tolerance=0.03)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]), 3)

    def test_notes_far_apart_form_separate_events(self):
        notes = [
            Note(pitch=40, start=0.0, end=0.5),
            Note(pitch=45, start=1.0, end=1.5),
        ]
        events = group_chord_events(notes, tolerance=0.03)
        self.assertEqual(len(events), 2)

    def test_empty_input(self):
        self.assertEqual(group_chord_events([]), [])


class TestAssignFrets(unittest.TestCase):
    def test_open_chord_e_major_like(self):
        # E2, B2(open-ish via A string fret2->B... just use simple open-string pitches
        # for a deterministic, easily-checked case: an open E-A-D chord.
        notes = [
            Note(pitch=40, start=0.0, end=1.0),  # E2 open on string 6
            Note(pitch=45, start=0.0, end=1.0),  # A2 open on string 5
            Note(pitch=50, start=0.0, end=1.0),  # D3 open on string 4
        ]
        assignments = assign_frets(notes, max_fret=19)
        self.assertEqual(len(assignments), 3)
        strings_used = [a.string for a in assignments]
        self.assertEqual(len(strings_used), len(set(strings_used)), "no string reused")
        for a in assignments:
            self.assertGreaterEqual(a.fret, 0)
            self.assertLessEqual(a.fret, 19)
            # pitch must actually match the chosen string/fret
            self.assertEqual(STANDARD_TUNING[a.string] + a.fret, a.note.pitch)

    def test_no_same_string_collision_within_chord_event(self):
        # A cluster of 5 simultaneous notes -- must not collide on any string.
        notes = [
            Note(pitch=p, start=0.0, end=1.0)
            for p in (40, 47, 52, 56, 60)  # arbitrary close-voiced chord
        ]
        assignments = assign_frets(notes, max_fret=19)
        strings_used = [a.string for a in assignments]
        self.assertEqual(len(strings_used), len(set(strings_used)))

    def test_respects_max_fret(self):
        notes = [Note(pitch=41, start=0.0, end=0.5)]  # F2, fret 1 on low E only w/ max_fret 0
        assignments = assign_frets(notes, max_fret=0)
        # fret 1 needed on every string that could reach it except none at max_fret=0
        # except pitch 41 - open string pitches are 40,45,50,55,59,64 -> fret=1 only on
        # string 6 (40+1=41). max_fret=0 forbids fret 1, so it should be dropped.
        self.assertEqual(assignments, [])

    def test_within_max_fret_bound(self):
        notes = [
            Note(pitch=p, start=i * 0.5, end=i * 0.5 + 0.4)
            for i, p in enumerate([40, 52, 64, 76, 45, 57, 69])
        ]
        assignments = assign_frets(notes, max_fret=19)
        for a in assignments:
            self.assertGreaterEqual(a.fret, 0)
            self.assertLessEqual(a.fret, 19)

    def test_too_many_simultaneous_notes_drops_excess_gracefully(self):
        # 7 simultaneous notes -- only 6 strings exist, so at most 6 can be placed.
        notes = [
            Note(pitch=p, start=0.0, end=1.0)
            for p in (40, 41, 42, 43, 44, 45, 46)
        ]
        assignments = assign_frets(notes, max_fret=19)
        self.assertLessEqual(len(assignments), 6)
        strings_used = [a.string for a in assignments]
        self.assertEqual(len(strings_used), len(set(strings_used)))

    def test_out_of_range_pitch_is_dropped_not_raised(self):
        notes = [
            Note(pitch=40, start=0.0, end=1.0),  # in range
            Note(pitch=10, start=0.0, end=1.0),  # far too low for any string
        ]
        assignments = assign_frets(notes, max_fret=19)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0].note.pitch, 40)

    def test_empty_notes_returns_empty(self):
        self.assertEqual(assign_frets([]), [])

    def test_dp_looks_ahead_to_upcoming_high_position_chord(self):
        # F4 alone, then a G#4+C#5 chord whose only tight voicing is both notes at
        # fret 9. A per-event greedy pick would put F4 at fret 1 (string 1) and pay
        # a huge hand jump; the DP must foresee the chord and keep F4 near fret 9
        # (string 3, fret 10).
        notes = [
            Note(pitch=65, start=0.0, end=0.4),   # F4
            Note(pitch=68, start=0.5, end=1.0),   # G#4
            Note(pitch=73, start=0.5, end=1.0),   # C#5
        ]
        assignments = assign_frets(notes, max_fret=19)
        f4 = next(a for a in assignments if a.note.pitch == 65)
        self.assertEqual((f4.string, f4.fret), (3, 10))

    def test_open_string_is_position_transparent(self):
        # An open-string note between two fret-9 chords must not drag the hand to
        # fret 0 and back: the open E is chosen (it's free), and both chords stay
        # at the fret-9 voicing.
        chord = [(68, 0.0), (73, 0.0), (68, 2.0), (73, 2.0)]
        notes = [Note(pitch=p, start=t, end=t + 0.4) for p, t in chord]
        notes.append(Note(pitch=64, start=1.0, end=1.4))  # E4: open string 1
        assignments = assign_frets(notes, max_fret=19)

        e4 = next(a for a in assignments if a.note.pitch == 64)
        self.assertEqual((e4.string, e4.fret), (1, 0))
        for a in assignments:
            if a.note.pitch in (68, 73):
                self.assertEqual(a.fret, 9)

    def test_multiple_chord_events_each_internally_collision_free(self):
        notes = [
            # event 1 at t=0
            Note(pitch=40, start=0.0, end=0.5),
            Note(pitch=45, start=0.0, end=0.5),
            Note(pitch=50, start=0.0, end=0.5),
            # event 2 at t=1.0
            Note(pitch=41, start=1.0, end=1.5),
            Note(pitch=46, start=1.0, end=1.5),
        ]
        assignments = assign_frets(notes, max_fret=19)
        events = group_chord_events(notes, tolerance=0.03)
        self.assertEqual(len(events), 2)

        # Re-group the returned assignments by onset and check each group is
        # collision-free (mirrors the assertion the pipeline itself relies on).
        by_start: dict = {}
        for a in assignments:
            by_start.setdefault(a.note.start, []).append(a.string)
        for start, strings in by_start.items():
            self.assertEqual(len(strings), len(set(strings)), f"collision at t={start}")


class TestRenderAsciiTab(unittest.TestCase):
    def test_empty_assignments_returns_placeholder(self):
        self.assertEqual(render_ascii_tab([]), "(no notes)\n")

    def test_open_e_chord_renders_zero_on_expected_strings(self):
        # Open E major-ish chord: low E (string 6, fret 0), B (string 2, fret 0),
        # high e (string 1, fret 0) all at t=0.
        notes = [
            Note(pitch=40, start=0.0, end=1.0),  # low E open -> string 6
            Note(pitch=59, start=0.0, end=1.0),  # B open -> string 2
            Note(pitch=64, start=0.0, end=1.0),  # high e open -> string 1
        ]
        assignments = assign_frets(notes, max_fret=19)
        text = render_ascii_tab(assignments)

        lines = [l for l in text.splitlines() if l]
        self.assertEqual(len(lines), 6)
        # One line per string, in high-e-to-low-E order, each starting with its label.
        self.assertEqual([l[0] for l in lines], ["e", "B", "G", "D", "A", "E"])
        # The three fretted strings should show "0"; the rest show only "-" fill.
        by_label = {l[0]: l for l in lines}
        self.assertIn("0", by_label["e"])
        self.assertIn("0", by_label["B"])
        self.assertIn("0", by_label["E"])
        self.assertNotIn("0", by_label["G"])
        self.assertNotIn("0", by_label["D"])
        self.assertNotIn("0", by_label["A"])

    def test_all_lines_have_equal_length(self):
        notes = [
            Note(pitch=40 + i, start=float(i), end=float(i) + 0.5) for i in range(5)
        ]
        assignments = assign_frets(notes, max_fret=19)
        lines = [l for l in render_ascii_tab(assignments).splitlines() if l]
        lengths = {len(l) for l in lines}
        self.assertEqual(len(lengths), 1, f"ragged tab lines: {lines}")


class TestCorrectTempoOctave(unittest.TestCase):
    def test_within_range_unchanged(self):
        self.assertAlmostEqual(correct_tempo_octave(100.0), 100.0)

    def test_double_error_is_halved(self):
        # The exact real-world symptom that motivated this: 199 BPM -> ~99.5 BPM.
        self.assertAlmostEqual(correct_tempo_octave(199.0), 99.5)

    def test_far_too_fast_is_halved_repeatedly(self):
        self.assertAlmostEqual(correct_tempo_octave(400.0), 100.0)

    def test_too_slow_is_doubled(self):
        self.assertAlmostEqual(correct_tempo_octave(40.0), 80.0)

    def test_zero_or_negative_passed_through(self):
        self.assertEqual(correct_tempo_octave(0.0), 0.0)
        self.assertEqual(correct_tempo_octave(-5.0), -5.0)

    def test_custom_range_respected(self):
        self.assertAlmostEqual(correct_tempo_octave(50.0, reasonable_range=(30.0, 300.0)), 50.0)


class TestMergeRepeatedNotes(unittest.TestCase):
    def test_zero_gap_same_pitch_notes_merge_into_one(self):
        # Mirrors the real bass-string symptom: one note ends exactly where the next
        # of the same pitch begins.
        notes = [
            Note(pitch=43, start=0.825, end=1.0909),
            Note(pitch=43, start=1.0909, end=1.2886),
            Note(pitch=43, start=1.2886, end=1.5682),
        ]
        merged = merge_repeated_notes(notes, max_gap=0.05)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].pitch, 43)
        self.assertAlmostEqual(merged[0].start, 0.825)
        self.assertAlmostEqual(merged[0].end, 1.5682)

    def test_notes_with_real_silence_gap_are_not_merged(self):
        # A deliberately re-picked note leaves an audible attack gap -- must NOT merge.
        notes = [
            Note(pitch=43, start=0.0, end=0.4),
            Note(pitch=43, start=0.6, end=1.0),  # 0.2s gap, well above max_gap
        ]
        merged = merge_repeated_notes(notes, max_gap=0.05)
        self.assertEqual(len(merged), 2)

    def test_different_pitch_does_not_merge(self):
        notes = [
            Note(pitch=43, start=0.0, end=0.5),
            Note(pitch=45, start=0.5, end=1.0),
        ]
        merged = merge_repeated_notes(notes, max_gap=0.05)
        self.assertEqual(len(merged), 2)

    def test_interleaved_different_pitch_does_not_block_merge(self):
        # A melody note on another string happening between two segments of the same
        # oversegmented bass note shouldn't prevent the bass note from merging.
        notes = [
            Note(pitch=43, start=0.0, end=0.5),
            Note(pitch=67, start=0.1, end=0.3),  # unrelated overlapping note
            Note(pitch=43, start=0.5, end=1.0),
        ]
        merged = merge_repeated_notes(notes, max_gap=0.05)
        pitches = sorted(n.pitch for n in merged)
        self.assertEqual(pitches, [43, 67])
        merged_43 = next(n for n in merged if n.pitch == 43)
        self.assertAlmostEqual(merged_43.start, 0.0)
        self.assertAlmostEqual(merged_43.end, 1.0)

    def test_empty_input(self):
        self.assertEqual(merge_repeated_notes([]), [])


if __name__ == "__main__":
    unittest.main()
