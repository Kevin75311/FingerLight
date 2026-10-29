"""Tests for score-informed audio verification (B6, D-16).

No audio hardware and no recording of the real PX-S1100 exist yet, so every signal here
is synthesised (see `_audio_synth.py`): additive partial series with decay, piano-like
inharmonicity, and a noise floor.  What matters is not "does it produce a set" but
whether it produces the *right* set under the specific failure mode the design doc calls
out -- an octave (or fifth) ghost -- and whether it still recognises a played note once it
has decayed a couple of seconds into its sustain.

Frame size: 8192 samples @ 22050 Hz (~371 ms) is used for the correctness tests below.
`test_semitone_resolution_needs_enough_frame` documents *why*: at smaller frames the
window's mainlobe is wide enough in Hz to blur adjacent semitones together, which is a
frequency-resolution limit, not a bug in the harmonic-matching logic -- see the module
docstring in `audio.py` and [[audio-verification-benchmark]] for the frame-size trade-off
against latency.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from fingerlight.audio import (  # noqa: E402
    DEFAULT_CONFIG,
    VerifierConfig,
    verify_frame,
)

from _audio_synth import make_signal, silence  # noqa: E402

SR = 22050
N = 8192

C4, D4, E4, F4, G4, A4, B4 = 60, 62, 64, 65, 67, 69, 71
C3, E3, G3 = 48, 52, 55
C5, G5, C6 = 72, 79, 84
A0, C8 = 21, 108


def _played(pitches, **kw):
    return make_signal(pitches, SR, N, **kw)


# ------------------------------------------------------------------ single notes


def test_single_note_detected():
    assert verify_frame(_played([C4]), SR, [C4]) == frozenset({C4})


def test_lowest_note_a0_detected():
    """A0 (21) is the bottom of the 88-key range -- worth its own case since a short
    frame has the least frequency resolution relative to its semitone spacing here."""
    assert verify_frame(_played([A0]), SR, [A0]) == frozenset({A0})


def test_highest_note_c8_detected():
    assert verify_frame(_played([C8]), SR, [C8]) == frozenset({C8})


def test_silence_reports_nothing():
    assert verify_frame(silence(SR, N), SR, [C4, E4, G4]) == frozenset()


def test_candidate_not_played_is_not_reported():
    """The candidate that's actually sounding (E4) isn't even asked about -- only C4
    is a candidate, and it is not sounding, so nothing should come back."""
    assert verify_frame(_played([E4]), SR, [C4]) == frozenset()


# ------------------------------------------------------------------ chords


def test_triad_all_three_reported():
    assert verify_frame(_played([C4, E4, G4]), SR, [C4, E4, G4]) == frozenset({C4, E4, G4})


@pytest.mark.parametrize(
    "chord",
    [
        [C3, E3, G3, C4],          # 4 notes, includes an octave (C3/C4)
        [C3, E3, G3, C4, E4],      # 5 notes
        [36, 43, C3, E3, G3, C4],  # 6 notes, low bass + octave
    ],
)
def test_wider_chords_all_reported(chord):
    assert verify_frame(_played(chord), SR, chord) == frozenset(chord)


def test_ten_candidate_chord():
    chord = [21, 33, 45, 57, 60, 64, 67, 72, 79, 84]
    assert verify_frame(_played(chord), SR, chord) == frozenset(chord)


def test_stacked_octaves_all_reported():
    """C3+C4+C5 -- three octaves of the same pitch class stacked, the harmonically
    densest case: every candidate's fundamental sits exactly on a lower candidate's
    harmonic.  All three are genuinely sounding and all three must come back."""
    chord = [C3, C4, C5]
    assert verify_frame(_played(chord), SR, chord) == frozenset(chord)


def test_only_played_subset_reported_from_larger_candidate_set():
    """Candidates are whatever the light-score says is expected right now (up to 10);
    only two of them are actually being played."""
    candidates = [55, 60, 64, 67, 72]
    assert verify_frame(_played([C4, G4]), SR, candidates) == frozenset({C4, G4})


# ------------------------------------------------------------------ the octave/fifth negative controls (design doc's #1 failure mode)


def test_octave_negative_control_lone_c5_does_not_imply_c4():
    """The exact scenario named in the design doc: a lone C5, with C4 and C5 both
    candidates.  C4 must NOT be reported -- C5's fundamental sits exactly on C4's 2nd
    harmonic, which is the textbook octave ghost."""
    result = verify_frame(_played([C5]), SR, [C4, C5])
    assert result == frozenset({C5})
    assert C4 not in result


def test_fifth_negative_control_lone_c5_does_not_imply_g4():
    result = verify_frame(_played([C5]), SR, [G4, C5])
    assert result == frozenset({C5})
    assert G4 not in result


def test_upward_ghost_lone_c4_does_not_imply_its_own_octave_or_fifth():
    """The other direction of the same failure mode: C4 alone has real energy at C5's
    and G5's fundamental frequencies (C4's own 2nd and 3rd harmonics) -- neither must be
    reported as separately sounding."""
    result = verify_frame(_played([C4]), SR, [C4, C5, G5])
    assert result == frozenset({C4})


def test_octave_inside_a_real_chord_is_not_swallowed():
    """The hard case the negative controls above don't cover: C3 AND C4 genuinely both
    sounding at once (an octave doubling within a chord).  A detector that reflexively
    treats "energy at a lower note's harmonic" as a ghost would wrongly drop C4 here --
    it must still be reported because it really is being struck, on top of C3's own
    harmonic energy at the same frequency."""
    chord = [C3, E3, G3, C4]
    result = verify_frame(_played(chord), SR, chord)
    assert C3 in result and C4 in result


def test_weakened_harmonic_corroboration_reintroduces_the_octave_ghost():
    """Evidence that the negative controls above are real tests, not ones that pass no
    matter what.  Ask about G5 alone (not C4 -- so the fundamental-gate explaining-away
    subtraction, which only fires for a *lower* candidate that gets evaluated first,
    never runs) while only C4 is actually sounding.  C4's 3rd harmonic sits right on G5's
    fundamental, so G5's fundamental gate passes on its own; harmonic corroboration is
    the only thing rejecting it.  With `min_harmonic_support` dropped to 0 (corroboration
    effectively disabled), G5 is wrongly reported; at the shipped default it correctly is
    not.  This pins corroboration as load-bearing for the upward ghost, independent of
    the subtraction mechanism."""
    sig = _played([C4])
    assert verify_frame(sig, SR, [G5]) == frozenset()  # shipped default: correct
    weak = VerifierConfig(min_harmonic_support=0.0)
    assert verify_frame(sig, SR, [G5], config=weak) == frozenset({G5})  # weakened: wrong


# ------------------------------------------------------------------ sustain / decay


def test_decayed_note_is_still_reported():
    """A note struck 2s ago is quieter but still well above the noise floor -- it must
    still be reported as sounding (thresholds here are SNR-relative, not absolute)."""
    sig = make_signal([C4], SR, N, t_offset=2.0)
    assert verify_frame(sig, SR, [C4]) == frozenset({C4})


def test_fully_decayed_note_is_correctly_not_reported():
    """6s in, the same note has decayed into the noise floor -- correctly absent, since
    at that point it genuinely is no longer audible."""
    sig = make_signal([C4], SR, N, t_offset=6.0)
    assert verify_frame(sig, SR, [C4]) == frozenset()


def test_quiet_chord_still_detected():
    """Overall level doesn't matter, only SNR over the noise floor -- a quiet chord
    (1/20 amplitude of the other tests) is detected exactly the same way."""
    chord = [C4, E4, G4]
    sig = make_signal(chord, SR, N, amplitude=0.05)
    assert verify_frame(sig, SR, chord) == frozenset(chord)


# ------------------------------------------------------------------ inharmonicity tolerance


def test_realistic_inharmonicity_is_tolerated():
    """Partials synthesised sharp of exact integer multiples (inharmonicity_b=0.0004,
    a plausible mid-register stiff-string value) are still matched by the default
    tolerance bands."""
    sig = make_signal([C4], SR, N, inharmonicity_b=0.0004)
    assert verify_frame(sig, SR, [C4]) == frozenset({C4})


def test_inharmonicity_tolerance_is_load_bearing():
    """A more strongly stretched partial series (inharmonicity_b=0.002 -- a plausible
    value for a short, stiff treble string, well beyond the 0.0004 used for the
    "realistic" case above) is still matched at the shipped default tolerance.  Collapse
    the tolerance to ~0 (a config that no longer accounts for inharmonicity at all) and
    the same signal fails harmonic corroboration and is missed -- proof the tolerance
    constants in `VerifierConfig` are actually doing something, not dead parameters."""
    sig = make_signal([C4], SR, N, inharmonicity_b=0.002)
    assert verify_frame(sig, SR, [C4]) == frozenset({C4})  # shipped default: correct
    zero_tolerance = VerifierConfig(base_cents=0.5, cents_per_harmonic=0.0)
    result = verify_frame(sig, SR, [C4], config=zero_tolerance)
    assert result == frozenset()  # tolerance disabled: the same note is now missed


# ------------------------------------------------------------------ frame-size / resolution limit (documented, not hidden)


def test_semitone_resolution_needs_enough_frame():
    """Adjacent semitones (C4/C#4) are NOT reliably separable at very short frames: the
    analysis window's mainlobe is wide enough in Hz, at 2048 samples @ 22050 Hz (~93 ms),
    to bleed C4's energy into C#4's fundamental band, and C#4 is falsely reported. At
    8192 samples (~371 ms) it resolves correctly. This is a frequency-resolution
    trade-off against latency, not a bug -- see [[audio-verification-benchmark]]."""
    Csharp4 = 61
    short = make_signal([C4], SR, 2048)
    long_ = make_signal([C4], SR, 8192)
    assert Csharp4 in verify_frame(short, SR, [C4, Csharp4])
    assert verify_frame(long_, SR, [C4, Csharp4]) == frozenset({C4})


# ------------------------------------------------------------------ misc API behaviour


def test_empty_candidates_returns_empty():
    assert verify_frame(_played([C4]), SR, []) == frozenset()


def test_rejects_candidate_outside_88_key_range():
    with pytest.raises(ValueError):
        verify_frame(_played([C4]), SR, [C4, 9])


# ------------------------------------------------------------------ TP/FP/FN sweep over a battery of cases


def test_detection_battery_true_false_positive_negative_counts():
    """Runs every case above's (played, candidates) pair through the verifier and tallies
    true positives / false positives / false negatives directly, so "0 false positives"
    in the report is backed by a printed case count, not just an absence of failures."""
    cases = [
        ("single C4", [C4], [C4]),
        ("single A0", [A0], [A0]),
        ("single C8", [C8], [C8]),
        ("silence", [], [C4, E4, G4]),
        ("wrong note", [E4], [C4]),
        ("triad", [C4, E4, G4], [C4, E4, G4]),
        ("chord4 w/ octave", [C3, E3, G3, C4], [C3, E3, G3, C4]),
        ("chord5", [C3, E3, G3, C4, E4], [C3, E3, G3, C4, E4]),
        ("chord6", [36, 43, C3, E3, G3, C4], [36, 43, C3, E3, G3, C4]),
        ("ten-candidate chord", [21, 33, 45, 57, 60, 64, 67, 72, 79, 84],
         [21, 33, 45, 57, 60, 64, 67, 72, 79, 84]),
        ("stacked octaves", [C3, C4, C5], [C3, C4, C5]),
        ("subset of larger candidate set", [C4, G4], [55, 60, 64, 67, 72]),
        ("octave negative control", [C5], [C4, C5]),
        ("fifth negative control", [C5], [G4, C5]),
        ("upward ghost control", [C4], [C4, C5, G5]),
        ("decayed 2s", [C4], [C4]),
        ("quiet chord", [C4, E4, G4], [C4, E4, G4]),
        ("realistic inharmonicity", [C4], [C4]),
    ]
    extra_kwargs = {"decayed 2s": {"t_offset": 2.0}, "quiet chord": {"amplitude": 0.05},
                     "realistic inharmonicity": {"inharmonicity_b": 0.0004}}

    tp = fp = fn = 0
    n_cases = 0
    for name, played, candidates in cases:
        n_cases += 1
        kw = extra_kwargs.get(name, {})
        sig = _played(played, **kw) if played else silence(SR, N)
        result = verify_frame(sig, SR, candidates)
        expected = frozenset(p for p in played if p in candidates)
        tp += len(result & expected)
        fp += len(result - expected)
        fn += len(expected - result)

    print("\nDetection battery: %d cases, TP=%d FP=%d FN=%d" % (n_cases, tp, fp, fn))
    assert n_cases == len(cases)
    assert fp == 0, "false positive somewhere in the battery"
    assert fn == 0, "false negative somewhere in the battery"
