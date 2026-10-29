"""Synthetic piano-like test signals for test_audio.py.

No audio hardware and no recording of the real instrument exist yet (see
[[00-measurements]]), so every signal fed to the verifier in tests is generated here by
additive synthesis: a decaying partial series per note, optionally detuned sharp of exact
integer multiples (piano inharmonicity), plus a small noise floor.  Not a test file
itself -- imported by test_audio.py -- but it lives under firmware/tests/ because it has
no reason to exist outside the test suite.
"""

from __future__ import annotations

import numpy as np

from fingerlight.audio import midi_to_freq


def synth_note(
    pitch: int,
    sample_rate: int,
    n_samples: int,
    *,
    amplitude: float = 1.0,
    n_partials: int = 8,
    t_offset: float = 0.0,
    inharmonicity_b: float = 0.0004,
    decay_tau: float = 1.2,
) -> np.ndarray:
    """One decaying piano-like tone.

    `t_offset` is time-since-onset at the start of the returned buffer -- used to
    simulate a note that was struck before the analysis window started (sustain/decay
    tests): a buffer with t_offset=2.0 is quieter throughout than one with t_offset=0,
    exactly as a real decaying string would be two seconds after the strike.

    Partial n's frequency is n*f0*sqrt(1 + inharmonicity_b*n**2) (the standard stiff-
    string model) and its amplitude decays both with partial number (1/n, higher
    partials are quieter, typical of a struck string) and with time
    (exp(-t/(decay_tau/n)), higher partials die out faster -- also typical).
    """
    f0 = midi_to_freq(pitch)
    t = t_offset + np.arange(n_samples) / sample_rate
    sig = np.zeros(n_samples)
    for n in range(1, n_partials + 1):
        fn = n * f0 * np.sqrt(1.0 + inharmonicity_b * n * n)
        if fn >= sample_rate / 2:
            break
        a_n = amplitude / n
        tau_n = decay_tau / n
        sig += a_n * np.exp(-t / tau_n) * np.sin(2.0 * np.pi * fn * t)
    return sig


def make_signal(
    pitches,
    sample_rate: int,
    n_samples: int,
    *,
    amplitude: float = 1.0,
    t_offset: float = 0.0,
    noise_level: float = 2e-4,
    seed: int = 0,
    **kwargs,
) -> np.ndarray:
    """Sum of `synth_note` for each pitch in `pitches`, plus Gaussian noise."""
    rng = np.random.default_rng(seed)
    sig = np.zeros(n_samples)
    for p in pitches:
        sig += synth_note(
            p, sample_rate, n_samples, amplitude=amplitude, t_offset=t_offset, **kwargs
        )
    sig += rng.normal(0.0, noise_level, n_samples)
    return sig


def silence(sample_rate: int, n_samples: int, *, noise_level: float = 2e-4, seed: int = 0):
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, noise_level, n_samples)
