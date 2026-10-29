#!/usr/bin/env python3
"""Benchmark for B6 score-informed audio verification (`fingerlight.audio.verify_frame`).

This exists to replace an assumption.  [[HANDOFF]] Sec.7 lists "audio verification runs
in realtime on a Zero 2 W" as **ASSUMED -- argued from operation count, not benchmarked**.
This script measures real wall-clock time per frame *on this PC* across the realistic
parameter range (sample rate, frame size, candidate count), then extrapolates a Zero 2 W
estimate from a stated, sourced scaling factor -- clearly separated from the measured
numbers, because it is a projection, not a measurement.  See
[[audio-verification-benchmark]] for the full write-up and what would need measuring on
real hardware to settle this properly.

Realtime factor = (processing time per frame) / (frame duration).  A factor well under
1.0 means the analysis for one frame finishes well before the next frame's worth of audio
has even arrived -- the only meaningful way to answer "does this fit," since bare
milliseconds mean nothing without the frame duration they have to beat.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "firmware", "src"))
from fingerlight.audio import verify_frame, midi_to_freq  # noqa: E402

# --------------------------------------------------------------------- test signal

def _make_chord_signal(pitches, sample_rate, n_samples, n_partials=6, seed=0):
    """A small self-contained additive-synthesis chord -- deliberately not imported from
    firmware/tests/_audio_synth.py, so this tool has no dependency on the test tree and
    keeps working if the tests are reorganised.  Realism only needs to be good enough to
    exercise the real fundamental+harmonic search and subtraction code paths (i.e. not
    silence, which returns early) -- FFT and array-scan cost depend on array size and
    candidate count, not on the exact waveform, so this need not match the more careful
    synthesis in the test suite."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples) / sample_rate
    sig = np.zeros(n_samples)
    for p in pitches:
        f0 = midi_to_freq(p)
        for n in range(1, n_partials + 1):
            fn = n * f0 * np.sqrt(1.0 + 0.0004 * n * n)
            if fn >= sample_rate / 2:
                break
            sig += (1.0 / n) * np.sin(2.0 * np.pi * fn * t)
    sig += rng.normal(0.0, 2e-4, n_samples)
    return sig


# --------------------------------------------------------------------- measurement

def _time_one_config(sample_rate, frame_size, n_candidates, repeats=200, warmup=20):
    """Median wall-clock seconds per `verify_frame` call for one (sample_rate,
    frame_size, n_candidates) point.  Candidates are the bottom `n_candidates` of a
    fixed 10-pitch spread and all of them are actually played, so every call walks the
    full fundamental+harmonic-search+subtraction path for every candidate -- the worst
    case the real caller would hit, not the early-exit silence path."""
    spread = [24, 36, 43, 48, 52, 55, 60, 64, 67, 72]  # ~2 octaves to just under 5, spanning bass->treble
    candidates = spread[:n_candidates]
    sig = _make_chord_signal(candidates, sample_rate, frame_size)

    for _ in range(warmup):
        verify_frame(sig, sample_rate, candidates)

    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        verify_frame(sig, sample_rate, candidates)
        samples.append(time.perf_counter() - t0)
    return float(np.median(samples))


# --------------------------------------------------------------------- Zero 2 W extrapolation

#: Source and method for this number -- read before trusting it.
#:
#: The Zero 2 W's Cortex-A53 @ 1 GHz has no measurement behind this scaling factor; it is
#: read off published Geekbench 5 single-core results for the two chip classes (Cortex-A53
#: @ ~1 GHz devices commonly score ~85-95; desktop Core i5 12th-gen parts commonly score
#: ~1750-1850), giving a per-core slowdown of roughly 18-22x, rounded here to a stated
#: band of 15x (optimistic) to 25x (pessimistic).  This is a per-core *benchmark* ratio,
#: not a clock-speed ratio (1 GHz vs. this PC's ~2.5-4.4 GHz would only be a 2.5-4.4x
#: gap) -- the extra slowdown comes from IPC and NEON vector width being much narrower
#: on the A53 than AVX2 on the desktop part, which matters here because `verify_frame`
#: is FFT- and array-scan-heavy, exactly the kind of code a wider vector unit accelerates.
#: numpy on Raspberry Pi OS also typically runs against a slower/no-BLAS build for FFT
#: (pocketfft, used by `numpy.fft`, is not BLAS-backed either way, which is *why* pure
#: Geekbench-style scalar/vector ratios are a more defensible proxy here than a BLAS
#: GFLOPS comparison would be). This is an ESTIMATE, not a measurement -- see the caveats
#: printed at the end of this script and in [[audio-verification-benchmark]].
ZERO2W_SCALING_OPTIMISTIC = 15.0
ZERO2W_SCALING_PESSIMISTIC = 25.0


def _cpu_friendly_name() -> str:
    """Best-effort friendly CPU name (e.g. "12th Gen Intel(R) Core(TM) i5-12400F") on
    top of `platform.processor()`'s bare family/model/stepping string.  Best-effort
    only: falls back silently if the platform-specific lookup isn't available."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor).Name"],
                capture_output=True, text=True, timeout=10,
            )
            name = out.stdout.strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def main():
    friendly = _cpu_friendly_name()
    print("CPU (this PC)   : %s%s" % (
        platform.processor() or platform.machine(),
        "  [%s]" % friendly if friendly else "",
    ))
    print("Platform        : %s" % platform.platform())
    print("numpy version   : %s" % np.__version__)
    print("Zero 2 W target : Cortex-A53 quad-core @ 1 GHz, 512 MB RAM (NOT measured here)")
    print()
    print("Scaling factor applied to PC numbers below to estimate the Zero 2 W: "
          "%.0fx (optimistic) .. %.0fx (pessimistic), from published Geekbench 5 "
          "single-core scores for the two chip classes -- see this file's module "
          "docstring / the ZERO2W_SCALING_* comment for the exact reasoning. "
          "This is an EXTRAPOLATION, not a hardware measurement."
          % (ZERO2W_SCALING_OPTIMISTIC, ZERO2W_SCALING_PESSIMISTIC))
    print()

    sample_rates = [16000, 22050, 44100]
    frame_sizes = [2048, 4096, 8192]
    candidate_counts = [1, 3, 5, 10]

    header = (
        "%8s %6s %5s | %10s %8s | %14s %14s | %s"
        % ("sr(Hz)", "frame", "cand", "PC ms/frame", "PC RT", "Zero2W ms (opt)",
           "Zero2W ms (pess)", "Zero2W RT (opt..pess)")
    )
    print(header)
    print("-" * len(header))

    worst_pess_rt = 0.0
    rows = []
    for sr in sample_rates:
        for frame in frame_sizes:
            frame_duration_s = frame / sr
            for n_cand in candidate_counts:
                pc_s = _time_one_config(sr, frame, n_cand)
                pc_ms = pc_s * 1000.0
                pc_rt = pc_s / frame_duration_s

                z_opt_ms = pc_ms * ZERO2W_SCALING_OPTIMISTIC
                z_pess_ms = pc_ms * ZERO2W_SCALING_PESSIMISTIC
                z_opt_rt = (pc_s * ZERO2W_SCALING_OPTIMISTIC) / frame_duration_s
                z_pess_rt = (pc_s * ZERO2W_SCALING_PESSIMISTIC) / frame_duration_s
                worst_pess_rt = max(worst_pess_rt, z_pess_rt)

                rows.append((sr, frame, n_cand, pc_ms, pc_rt, z_opt_ms, z_pess_ms,
                             z_opt_rt, z_pess_rt))
                print(
                    "%8d %6d %5d | %10.3f %8.4f | %14.3f %14.3f | %.4f..%.4f"
                    % (sr, frame, n_cand, pc_ms, pc_rt, z_opt_ms, z_pess_ms,
                       z_opt_rt, z_pess_rt)
                )

    print()
    print("PC realtime factor  = PC processing time / frame duration (must be << 1.0)")
    print("Zero2W realtime factor = PC time * scaling factor / frame duration -- "
          "EXTRAPOLATED, see caveats below.")
    print()

    # A realistic B6 operating point: enough resolution to separate adjacent semitones
    # in the octave the practice piece is in (see test_semitone_resolution_needs_enough_
    # frame in firmware/tests/test_audio.py) without an excessive analysis window --
    # 4096 samples @ 22050 Hz (~186 ms) is a reasonable middle ground, at a realistic
    # chord size of up to 5 simultaneous candidates.
    op_sr, op_frame, op_cand = 22050, 4096, 5
    op_row = next(r for r in rows if r[0] == op_sr and r[1] == op_frame and r[2] == op_cand)
    _, _, _, pc_ms, pc_rt, z_opt_ms, z_pess_ms, z_opt_rt, z_pess_rt = op_row
    print("Representative operating point: sr=%d Hz, frame=%d samples (%.0f ms), "
          "%d candidates" % (op_sr, op_frame, 1000 * op_frame / op_sr, op_cand))
    print("  PC:      %.3f ms/frame, realtime factor %.4f" % (pc_ms, pc_rt))
    print("  Zero2W:  %.3f..%.3f ms/frame (EXTRAPOLATED), realtime factor %.4f..%.4f"
          % (z_opt_ms, z_pess_ms, z_opt_rt, z_pess_rt))
    print()

    if worst_pess_rt < 1.0:
        print("CONCLUSION (extrapolated): every measured configuration's pessimistic "
              "Zero 2 W estimate stays under a realtime factor of 1.0 -- the extrapolation "
              "does not rule out realtime operation.  This is NOT the same as "
              "confirming it; see caveats.")
    else:
        over = [r for r in rows if r[8] >= 1.0]
        print("CONCLUSION (extrapolated): %d of %d configurations exceed a realtime "
              "factor of 1.0 on the PESSIMISTIC Zero 2 W estimate -- at those settings "
              "(larger frames and/or more candidates), the extrapolation says this "
              "would NOT keep up in realtime on a Zero 2 W. A negative result here is "
              "informative: it says which combinations to avoid, or that frame size / "
              "candidate count / sample rate need to be kept modest." % (len(over), len(rows)))

    print()
    print("CAVEATS -- read before trusting the Zero 2 W numbers above:")
    print("  1. The scaling factor is read off PUBLISHED Geekbench 5 single-core scores")
    print("     for the two chip *classes*, not measured on this project's actual Pi.")
    print("  2. It assumes verify_frame's numpy calls (rfft, elementwise array ops) scale")
    print("     with general-purpose single-core throughput; a Pi-specific numpy build")
    print("     (different pocketfft build, no BLAS acceleration relevant here since FFT")
    print("     doesn't use BLAS) could scale differently in either direction.")
    print("  3. It ignores thermal throttling, OS/audio-driver scheduling jitter, and")
    print("     contention from whatever else is running on the Pi (LED driving, MIDI")
    print("     ingest, the practice loop) -- all real, all unmeasured here.")
    print("  4. To replace this extrapolation with a measurement: run this exact script")
    print("     on the actual Zero 2 W (numpy is pip-installable on Raspberry Pi OS) and")
    print("     compare its printed CPU/numpy line to this one.")


if __name__ == "__main__":
    main()
