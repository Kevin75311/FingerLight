#!/usr/bin/env python
"""CLI entrypoint: song.mp3 -> guitar TAB (.gp5) or piano MIDI [+ optional PDF].

Usage:
    python run_pipeline.py song.mp3
    python run_pipeline.py song.mp3 --model htdemucs --max-fret 19
    python run_pipeline.py song.mp3 --instrument piano
    python run_pipeline.py song.mp3 --skip-render
    python run_pipeline.py song.mp3 --debug
    python run_pipeline.py song.mp3 --debug --minimum-frequency 82 --maximum-frequency 1320

See README.md for setup (submodules, MuseScore) and CLAUDE.md for architecture.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running straight from a repo checkout without requiring `pip install -e .`
# first (the editable install is still recommended -- see README).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from scoreforge.pipeline import run_pipeline  # noqa: E402
from scoreforge.tab import DEFAULT_MERGE_GAP  # noqa: E402


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description=(
            "Generate a guitar TAB (.gp5) or piano sheet music from a song: Demucs "
            "source separation -> Basic Pitch transcription -> (guitar) custom fret "
            "assignment + PyGuitarPro .gp5, or (piano) cleaned-up MIDI -> optional "
            "MuseScore PDF render."
        ),
    )
    parser.add_argument("song", help="Path to the input song audio file (e.g. song.mp3)")
    parser.add_argument(
        "--instrument",
        choices=["guitar", "piano"],
        default="guitar",
        help=(
            "Target instrument (default: guitar). guitar builds a .gp5 TAB via fret "
            "assignment. piano skips fret assignment (no absolute-pitch representation "
            "in .gp5) and instead writes a cleaned-up MIDI file for MuseScore to render "
            "directly to grand-staff notation; automatically switches --model to "
            "htdemucs_6s (for its dedicated piano stem) unless --model is set explicitly."
        ),
    )
    parser.add_argument(
        "--source",
        choices=["mix", "solo"],
        default="mix",
        help=(
            "What the recording is (default: mix). mix = full mix (band / vocal "
            "song): Demucs separates it and the target instrument's stem is "
            "transcribed. solo = the recording already is the solo target "
            "instrument (pure guitar / pure piano): separation is skipped entirely "
            "-- much faster, and more accurate (no separation artifacts)."
        ),
    )
    parser.add_argument(
        "--vocal-melody",
        action="store_true",
        dest="vocal_melody",
        help=(
            "Also transcribe the vocals stem and reduce it to a monophonic main-"
            "melody line (mix source only). guitar: written as an extra "
            "<song>.melody.gp5 + .tab.txt; piano: added as a separate melody track "
            "inside the piano MIDI (its own staff in MuseScore)."
        ),
    )
    parser.add_argument(
        "--model", default="htdemucs", help="Demucs separation model (default: htdemucs)"
    )
    parser.add_argument(
        "--no-reuse-stems",
        action="store_true",
        dest="no_reuse_stems",
        help=(
            "Force Demucs to re-run even if this song's stems already exist from a "
            "prior run (by default they are reused -- separation is the slowest stage)."
        ),
    )
    parser.add_argument(
        "--max-fret",
        type=int,
        default=19,
        dest="max_fret",
        help="Maximum fret considered when assigning notes to strings (guitar only, default: 19)",
    )
    parser.add_argument(
        "--skip-render",
        action="store_true",
        dest="skip_render",
        help="Skip the optional MuseScore PDF render stage",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help=(
            "Parallel worker threads for Demucs separation (default: "
            "min(4, cpu_count)). Only helps on CPU-only PyTorch installs; each job "
            "holds a model copy in memory, so don't set this above your RAM budget. "
            "Pass 0 to use Demucs's own serial default."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        dest="output_dir",
        help="Root directory for all pipeline outputs (default: ./output)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Write per-stage inspection artifacts for diagnosing quality issues: all "
            "4 Demucs stems (already always written, just logged explicitly), a "
            "Basic Pitch sonified render-back .wav + raw note-events .csv, and a "
            "fret-assignment .debug.csv with per-note string/fret/measure/tempo info "
            "(including dropped notes and why)."
        ),
    )
    transcribe_group = parser.add_argument_group(
        "Basic Pitch tuning (passthrough to its own CLI flags, unset = its defaults)"
    )
    transcribe_group.add_argument(
        "--onset-threshold", type=float, default=None, dest="onset_threshold",
        help="Minimum likelihood (0-1) for a note onset to register. Raise to cut extra/spurious notes.",
    )
    transcribe_group.add_argument(
        "--frame-threshold", type=float, default=None, dest="frame_threshold",
        help="Minimum likelihood (0-1) for a frame to sustain a note.",
    )
    transcribe_group.add_argument(
        "--minimum-note-length", type=float, default=None, dest="minimum_note_length",
        help="Minimum note length in milliseconds; filters out tiny spurious blips.",
    )
    transcribe_group.add_argument(
        "--minimum-frequency", type=float, default=None, dest="minimum_frequency",
        help="Minimum note frequency in Hz. Guitar low E is ~82Hz -- set this to reject "
             "lower-frequency bleed (bass/kick) being misheard as notes.",
    )
    transcribe_group.add_argument(
        "--maximum-frequency", type=float, default=None, dest="maximum_frequency",
        help="Maximum note frequency in Hz. Guitar's practical range tops out around "
             "1300-1500Hz depending on frets/tuning.",
    )

    tab_group = parser.add_argument_group("Fret-assignment tuning")
    tab_group.add_argument(
        "--merge-gap", type=float, default=DEFAULT_MERGE_GAP, dest="merge_gap",
        help=(
            f"Merge consecutive same-pitch notes separated by <= this many seconds "
            f"(default: {DEFAULT_MERGE_GAP}) into one sustained note. Fixes Basic "
            f"Pitch splitting one long sustained note (esp. bass) into several "
            f"repeated short ones -- confirmed by note pairs starting at exactly the "
            f"prior note's end time. A real re-picked note leaves a bigger gap and "
            f"won't merge."
        ),
    )
    tab_group.add_argument(
        "--no-merge-repeated-notes", action="store_true", dest="no_merge",
        help="Disable the merge-gap step entirely (keep every note as transcribed).",
    )
    tab_group.add_argument(
        "--skip-chords", action="store_true", dest="skip_chords",
        help=(
            "Skip madmom chord detection (chord-per-measure, capo suggestion, and "
            "the chord/note cross-check). Also happens automatically if madmom isn't "
            "installed."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    result = run_pipeline(
        song_path=Path(args.song),
        output_root=Path(args.output_dir),
        model=args.model,
        max_fret=args.max_fret,
        skip_render=args.skip_render,
        jobs=args.jobs,
        debug=args.debug,
        onset_threshold=args.onset_threshold,
        frame_threshold=args.frame_threshold,
        minimum_note_length=args.minimum_note_length,
        minimum_frequency=args.minimum_frequency,
        maximum_frequency=args.maximum_frequency,
        merge_gap=None if args.no_merge else args.merge_gap,
        skip_chords=args.skip_chords,
        instrument=args.instrument,
        source=args.source,
        vocal_melody=args.vocal_melody,
        reuse_stems=not args.no_reuse_stems,
    )

    print("\nDone. Output files:")
    for stem_name, path in result["stems"].items():
        print(f"  stem[{stem_name}]: {path}")
    print(f"  midi: {result['midi']}")
    if result["sonified_wav"]:
        print(f"  sonified (render-back) audio: {result['sonified_wav']}")
    if result["note_events_csv"]:
        print(f"  raw note events CSV: {result['note_events_csv']}")
    if result.get("gp5"):
        print(f"  gp5:  {result['gp5']}")
    if result.get("tab_txt"):
        print(f"  tab:  {result['tab_txt']}")
    if result.get("piano_midi"):
        print(f"  piano midi: {result['piano_midi']}")
    if result.get("melody_gp5"):
        print(f"  vocal melody gp5: {result['melody_gp5']}")
        print(f"  vocal melody tab: {result['melody_tab_txt']}")
    if result.get("melody_note_count"):
        print(f"  vocal melody notes: {result['melody_note_count']}")
    if result["debug_csv"]:
        print(f"  fret-assignment debug CSV: {result['debug_csv']}")
    if result["pdf"]:
        print(f"  pdf:  {result['pdf']}")
    else:
        print("  pdf:  (skipped)")

    capo = result.get("capo_suggestion") or {}
    if capo.get("total"):
        print(f"\nSuggested capo: fret {capo['capo']} -> "
              f"{capo['open_shape_count']}/{capo['total']} chords become open shapes.")
        if result.get("chord_mismatch_count"):
            print(f"chord/note mismatches flagged: {result['chord_mismatch_count']} measure(s) "
                  f"(see the chord chart at the top of the .tab.txt, or the debug CSV with --debug)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
