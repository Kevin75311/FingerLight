"""Score-informed audio verification (B6, decision D-16).

This is the audio fallback for knowing what was actually played when MIDI is
unavailable (LINE OUT into the Pi's audio input).  It is deliberately **not** blind
polyphonic transcription: during practice the current light-score position already
narrows what could be sounding to <=10 known MIDI pitches (`Event.pitches` in
`lightscore.py`), so the question this module answers is "of these known candidates,
which are actually sounding right now" -- a small per-pitch harmonic filterbank matched
against a piano-like partial template, not a general note estimator.  D-16.

Dependency choice: **numpy only**.  scipy was considered and rejected -- everything here
is an rfft and a handful of peak look-ups in a magnitude spectrum, which is a few lines
of numpy and does not need scipy.signal's filter-design machinery.  Skipping scipy keeps
the Pi-side dependency footprint smaller for no loss of capability.  Whether the *compute
budget* fits on a Zero 2 W is a separate, measured question -- see `tools/bench_audio.py`
and [[audio-verification-benchmark]] in the vault; do not assume it from this file alone.

Algorithm
---------
1. Window the frame (4-term Blackman-Harris -- see `_blackman_harris`, chosen for its
   low sidelobes, not Hann's, to control spectral leakage) and take an rfft magnitude
   spectrum.
2. Noise floor = the *median* magnitude of that spectrum.  Median rather than mean
   because a spectrum with a few strong partials in it has a mean pulled well above the
   true floor; the median is dominated by the many bins that carry no energy at all and
   tracks whatever floor is actually present (mic self-noise, line hum, room noise)
   without needing it calibrated in advance.
3. If the frame's RMS is below `silence_rms`, return no detections immediately -- there
   is nothing there to reason about, and it avoids chasing noise-floor artifacts in a
   near-silent frame.
4. Candidates are evaluated **lowest pitch first**, against a *residual* copy of the
   spectrum.  For each candidate:
   a. **Fundamental gate.**  Look for a peak within a tolerance band around the
      candidate's own fundamental frequency.  This must clear `fundamental_snr` (a
      multiple of the noise floor, not an absolute level -- see "sustain/decay" below).
      This is the gate that stops a *lower* note's upper harmonic from being read as a
      higher note's fundamental: playing C5 alone puts real energy at 2x*f0(C4), but
      puts none at 1x*f0(C4), so C4 fails this gate outright regardless of what its
      harmonics look like.
   b. **Harmonic corroboration.**  Of the candidate's own harmonics 2..n_harmonics that
      still fit under the Nyquist frequency, require at least `min_harmonic_support` of
      them (as a fraction) to also show a peak above `harmonic_snr` in the residual.
      This is what stops the *other* direction of the same failure mode: playing C4
      alone puts real energy near G5's fundamental too (C4's 3rd harmonic lands within a
      few cents of G5), which would pass G5's fundamental gate on its own -- but G5's
      *own* higher harmonics (2x, 3x G5) line up with C4's 6th, 9th, ... harmonics, which
      have decayed into the noise floor on a real piano tone, so the corroboration
      fraction fails and G5 is correctly rejected.
   c. If both gates pass, the candidate is "sounding": its measured partials (the peaks
      actually found for its fundamental and each corroborating harmonic) are subtracted
      out of the *residual* spectrum before the next (higher) candidate is evaluated.
      This explaining-away step is what a constrained NMF would give you as its
      sparsity-driven residual update; here it is a direct greedy subtraction because the
      template set is small and known (<=10 candidates), so there is no need for an
      iterative solver.
5. Return the set of detected pitches.

Piano inharmonicity.  Real piano partials are sharp of exact integer multiples of f0,
increasingly so at higher partial numbers and in the low bass (stiff strings act like
short stiff bars, not ideal strings).  Rather than fit a per-pitch inharmonicity
coefficient (`B` in the standard f_n = n*f0*sqrt(1 + B*n^2) model) -- which needs
calibrating against a real instrument this project does not yet have measurements from
(see [[00-measurements]]) -- the search band around each harmonic is a **tolerance in
cents that widens with harmonic number**: `cents_tolerance(n) = base_cents +
(n-1)*cents_per_harmonic`.  This absorbs the sharpening empirically (later partials get a
wider net) without asserting a stiffness constant nobody has measured.  If the real
instrument turns out to need a wider or differently-shaped tolerance, these two constants
are what to retune -- see `test_inharmonicity_tolerance_is_load_bearing` for a
demonstration of what happens if the tolerance is set too tight.

Detection threshold and sustain/decay.  Every threshold here (`fundamental_snr`,
`harmonic_snr`) is a **ratio over the per-frame noise floor**, not an absolute sample
magnitude.  A note struck two seconds ago and now well into its decay is quieter in
absolute terms but its partials are still well above the noise floor -- exactly what the
SNR-relative threshold is designed to keep catching, right up until the note has decayed
into the noise floor, which is the point at which "no longer sounding" becomes the
correct answer anyway.  This module has no memory across frames (by design -- pure,
frame-in-frame-out, no state to get wedged); a caller doing continuous verification is
expected to call it once per hop and can OR detections across a short window if it wants
attack-transient robustness.  That is a caller concern, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

MIDI_MIN = 21
MIDI_MAX = 108


def midi_to_freq(pitch: int) -> float:
    """A440 equal temperament, MIDI note 69 = A4 = 440 Hz."""
    return 440.0 * 2.0 ** ((pitch - 69) / 12.0)


@dataclass(frozen=True)
class VerifierConfig:
    """Tunable knobs.  Defaults are chosen for correctness against the synthetic test
    suite in `firmware/tests/test_audio.py`, not measured against a real instrument
    (there is no recording of the actual piano yet -- see [[00-measurements]]).  Retune
    against real captures once they exist.
    """

    n_harmonics: int = 6            # candidate's own partial series searched, 1..n
    base_cents: float = 35.0        # search-band half-width at the fundamental
    cents_per_harmonic: float = 10.0  # extra half-width per additional harmonic number
    fundamental_snr: float = 4.0    # peak/noise-floor ratio required at the fundamental
    harmonic_snr: float = 3.0       # peak/noise-floor ratio required to count a harmonic
    min_harmonic_support: float = 0.5  # fraction of *available* harmonics 2..n required
    silence_rms: float = 1e-6       # frames quieter than this report nothing


DEFAULT_CONFIG = VerifierConfig()


def _cents_tolerance(n: int, config: VerifierConfig) -> float:
    return config.base_cents + (n - 1) * config.cents_per_harmonic


def _band(freq: float, cents: float, min_half_width_hz: float = 0.0) -> tuple[float, float]:
    """[lo, hi] search band around `freq`.  The cents-derived half-width is clamped to at
    least `min_half_width_hz` so the band cannot come out *narrower than one FFT bin* --
    which it otherwise would for a low fundamental on a short frame (e.g. A0 = 27.5 Hz
    against a 5.4 Hz-resolution spectrum: a 35-cent band there is ~1.1 Hz wide, narrower
    than the gap between bins, and would miss every bin outright)."""
    ratio = 2.0 ** (cents / 1200.0)
    lo, hi = freq / ratio, freq * ratio
    if min_half_width_hz > 0.0:
        lo = min(lo, freq - min_half_width_hz)
        hi = max(hi, freq + min_half_width_hz)
    return lo, hi


def _peak_in_band(
    freqs: np.ndarray, mag: np.ndarray, lo: float, hi: float
) -> tuple[float, np.ndarray]:
    """Max magnitude within [lo, hi], and the bin indices spanning the band.  (0.0, empty
    array) if the band is out of range (e.g. a harmonic above Nyquist) or empty."""
    empty = np.empty(0, dtype=int)
    if hi <= 0.0 or lo >= freqs[-1]:
        return 0.0, empty
    idx = np.nonzero((freqs >= lo) & (freqs <= hi))[0]
    if idx.size == 0:
        return 0.0, empty
    return float(np.max(mag[idx])), idx


#: 4-term Blackman-Harris window coefficients.  Used instead of numpy's built-in Hann
#: (first sidelobe -31 dB) because a struck note's fundamental peak leaks enough energy
#: through a Hann window's sidelobes, 50-100 Hz away, to false-trigger an unrelated
#: candidate's fundamental gate against the median noise floor -- caught by
#: `test_no_false_trigger_from_a_different_note` after it failed under Hann.
#: Blackman-Harris's first sidelobe is -92 dB, about 60 dB (1000x) quieter, at the cost
#: of a slightly wider mainlobe than Hann -- an acceptable trade since the search bands
#: here are already tolerance windows, not single-bin lookups.
_BH_A = (0.35875, 0.48829, 0.14128, 0.01168)


def _blackman_harris(n: int) -> np.ndarray:
    if n <= 1:
        return np.ones(max(n, 0))
    i = np.arange(n)
    a0, a1, a2, a3 = _BH_A
    return (
        a0
        - a1 * np.cos(2 * np.pi * i / (n - 1))
        + a2 * np.cos(4 * np.pi * i / (n - 1))
        - a3 * np.cos(6 * np.pi * i / (n - 1))
    )


def _spectrum(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(samples, dtype=np.float64)
    n = x.size
    window = _blackman_harris(n) if n > 1 else np.ones(n)
    spec = np.fft.rfft(x * window)
    mag = np.abs(spec)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    return freqs, mag


def verify_frame(
    samples: np.ndarray,
    sample_rate: int,
    candidates: Iterable[int],
    config: VerifierConfig = DEFAULT_CONFIG,
) -> frozenset[int]:
    """Of `candidates` (MIDI pitches expected right now), return the subset judged
    currently sounding in this one frame of audio.

    `samples` is a 1-D array of mono audio (any real dtype; cast to float64
    internally).  Candidates outside 21..108 are rejected -- the light-score reader
    already guarantees this range, so a caller passing something else has a bug upstream.
    """
    cand = sorted(set(candidates))
    for p in cand:
        if not (MIDI_MIN <= p <= MIDI_MAX):
            raise ValueError("candidate pitch %r outside %d..%d" % (p, MIDI_MIN, MIDI_MAX))
    if not cand:
        return frozenset()

    x = np.asarray(samples, dtype=np.float64)
    if x.size == 0:
        return frozenset()
    rms = float(np.sqrt(np.mean(np.square(x))))
    if rms < config.silence_rms:
        return frozenset()

    freqs, mag = _spectrum(x, sample_rate)
    noise_floor = float(np.median(mag))
    if noise_floor <= 0.0:
        noise_floor = float(np.max(mag)) * 1e-6 + 1e-30  # degenerate (near-silent) frame

    # Two views of the same spectrum, kept deliberately separate:
    #  - `fundamental_residual` has a lower confirmed candidate's harmonics explained
    #    away, so a higher candidate's *fundamental* gate cannot be satisfied merely by
    #    sitting on top of one of those harmonics (the octave/fifth ghost).
    #  - `mag` (unmodified) is what harmonic *corroboration* is checked against, so a
    #    real chord tone whose own harmonic happens to coincide with a lower note's
    #    harmonic still gets credit for it -- that energy is genuinely there, doubly
    #    reinforced, and erasing it produced false negatives on ordinary triads (a C
    #    major chord's G is a case in point: C's 3rd harmonic sits right on G's 2nd).
    fundamental_residual = mag.copy()
    detected: set[int] = set()
    bin_res = float(freqs[1] - freqs[0]) if freqs.size > 1 else 0.0

    for p in cand:  # lowest pitch first -- see module docstring, algorithm step 4
        f0 = midi_to_freq(p)
        lo1, hi1 = _band(f0, _cents_tolerance(1, config), bin_res)
        a1, band1 = _peak_in_band(freqs, fundamental_residual, lo1, hi1)
        if a1 < config.fundamental_snr * noise_floor:
            continue

        support_hits = 0
        available = 0
        # (harmonic number, band) for every matched harmonic, fundamental included --
        # used below to *predict*, not just detect, each harmonic's contribution.
        matched: list[tuple[int, np.ndarray]] = [(1, band1)]
        for n in range(2, config.n_harmonics + 1):
            fn = n * f0
            if fn >= freqs[-1]:
                break
            available += 1
            lo, hi = _band(fn, _cents_tolerance(n, config), bin_res)
            an, band = _peak_in_band(freqs, mag, lo, hi)
            if band.size and an >= config.harmonic_snr * noise_floor:
                support_hits += 1
                matched.append((n, band))

        support_fraction = 1.0 if available == 0 else support_hits / available
        if support_fraction < config.min_harmonic_support:
            continue

        detected.add(p)
        # Explain this evidence away in the fundamental-gate view only -- but only the
        # *predicted* contribution, not the whole band, and only there.  A piano
        # partial's amplitude falls off roughly as 1/n (a rule of thumb, not a measured
        # constant -- see the module docstring); subtracting `a1/n` off each matched
        # band, rather than zeroing it to the noise floor, is what tells a pure octave
        # ghost (a higher candidate sitting on a lower note's harmonic, nothing else
        # there) apart from a chord where that higher note is *also* genuinely struck:
        # the ghost's band has ~nothing left after the predicted amount is removed, the
        # real note's band still has the excess energy the prediction didn't account
        # for, and its own fundamental gate can still clear.
        for n, band in matched:
            predicted = a1 / n
            fundamental_residual[band] = np.maximum(
                noise_floor, fundamental_residual[band] - predicted
            )

    return frozenset(detected)
