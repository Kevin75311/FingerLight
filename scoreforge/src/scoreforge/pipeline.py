"""Orchestrates the full song -> sheet-music pipeline:

    song.mp3 -> Demucs (separate; skipped for solo recordings)
             -> Basic Pitch / bytedance piano model (transcribe)
             -> madmom (chord detection, guitar only, optional)
             -> fret assignment + PyGuitarPro (guitar tab) or cleaned-up MIDI (piano)
             -> optional vocal-melody extraction from the vocals stem
             -> MuseScore (optional render)

All intermediates are written under ``<output_root>/<song_stem>/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional

from .chord import ChordSegment, detect_chords
from .melody import reduce_to_melody
from .render import render_pdf
from .separate import GUITAR_STEM_NAME, PIANO_STEM_NAME, separate
from .tab import (
    DEFAULT_MERGE_GAP,
    build_gp5_from_midi,
    build_gp5_from_notes,
    build_piano_midi,
    load_notes_from_midi,
    merge_repeated_notes,
)
from .transcribe import transcribe, transcribe_piano

INSTRUMENTS = ("guitar", "piano")

# How the input recording should be treated:
#   mix  -- full mix (band / vocal song): run Demucs and transcribe the target
#           instrument's stem.
#   solo -- the recording already *is* the solo target instrument (pure guitar /
#           pure piano): skip separation entirely and transcribe the input
#           directly. Much faster (separation is the slowest stage) and more
#           accurate (no separation artifacts).
SOURCES = ("mix", "solo")

# GM program used for the extracted vocal-melody track in piano-mode MIDI output.
_MELODY_GM_PROGRAM = 52  # Choir Aahs


def run_pipeline(
    song_path: Path,
    output_root: Path = Path("output"),
    model: str = "htdemucs",
    max_fret: int = 19,
    skip_render: bool = False,
    jobs: int = 0,
    debug: bool = False,
    onset_threshold: Optional[float] = None,
    frame_threshold: Optional[float] = None,
    minimum_note_length: Optional[float] = None,
    minimum_frequency: Optional[float] = None,
    maximum_frequency: Optional[float] = None,
    merge_gap: Optional[float] = DEFAULT_MERGE_GAP,
    skip_chords: bool = False,
    instrument: str = "guitar",
    source: str = "mix",
    vocal_melody: bool = False,
    reuse_stems: bool = True,
    progress: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, object]:
    """Run the full pipeline. See run_pipeline.py --help for parameter semantics.

    ``progress``, if given, is called as ``progress(stage_id, message)`` at the
    start of each stage -- the web GUI uses this to stream per-stage status; the
    CLI path just prints.
    """
    if instrument not in INSTRUMENTS:
        raise ValueError(f"instrument must be one of {INSTRUMENTS}, got {instrument!r}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")

    song_path = Path(song_path)
    if not song_path.exists():
        raise FileNotFoundError(f"Input song not found: {song_path}")

    if vocal_melody and source == "solo":
        print("[pipeline] Note: --vocal-melody needs a vocals stem, which a solo "
              "recording doesn't have -- ignoring it for this run.")
        vocal_melody = False

    def report(stage: str, message: str) -> None:
        print(f"[pipeline] {message}")
        if progress is not None:
            progress(stage, message)

    song_dir = Path(output_root) / song_path.stem
    song_dir.mkdir(parents=True, exist_ok=True)

    if instrument == "piano" and source == "mix" and model == "htdemucs":
        model = "htdemucs_6s"
        print("[pipeline] instrument=piano: switching Demucs model to htdemucs_6s "
              "(the default htdemucs model has no dedicated piano stem).")

    # ------------------------------------------------------------------ separate
    stems: Dict[str, Path] = {}
    vocals_stem: Optional[Path] = None
    if source == "solo":
        report("separate", "Stage 1: separation skipped (source=solo -- the input "
               "is already a solo instrument recording).")
        instrument_stem = song_path
    else:
        report("separate", f"Stage 1: separating stems with demucs ({model})...")
        stems = separate(song_path, song_dir / "separated", model=model, jobs=jobs,
                         reuse_existing=reuse_stems)
        stem_name = PIANO_STEM_NAME if instrument == "piano" else GUITAR_STEM_NAME
        instrument_stem = stems[stem_name]
        vocals_stem = stems.get("vocals")
        print(f"[pipeline]   -> {instrument} stem ({stem_name}): {instrument_stem}")
        if debug:
            for name, path in stems.items():
                print(f"[pipeline]   -> stem[{name}]: {path}")

    # ---------------------------------------------------------------- transcribe
    report("transcribe", f"Stage 2: transcribing {instrument} to MIDI...")
    transcribe_kwargs = dict(
        debug=debug,
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        minimum_note_length=minimum_note_length,
        minimum_frequency=minimum_frequency,
        maximum_frequency=maximum_frequency,
    )
    if instrument == "piano":
        transcribe_outputs = transcribe_piano(
            instrument_stem, song_dir / "midi", **transcribe_kwargs
        )
    else:
        transcribe_outputs = transcribe(
            instrument_stem, song_dir / "midi", **transcribe_kwargs
        )
    midi_path = transcribe_outputs["midi"]
    print(f"[pipeline]   -> MIDI ({transcribe_outputs['backend']}): {midi_path}")
    if debug and transcribe_outputs["sonified_wav"]:
        print(f"[pipeline]   -> sonified (render-back) audio: {transcribe_outputs['sonified_wav']}")
        print(f"[pipeline]   -> raw note events CSV: {transcribe_outputs['note_events_csv']}")

    # -------------------------------------------------------------------- chords
    chord_segments: Optional[List[ChordSegment]] = None
    if instrument == "piano":
        # Chord info is only embedded in the guitar tab output; running madmom for
        # piano was pure wasted compute.
        pass
    elif skip_chords:
        report("chords", "Stage 3: chord detection skipped (--skip-chords)")
    else:
        report("chords", "Stage 3: detecting chords with madmom "
               "(independent of the MIDI transcription above)...")
        try:
            chord_segments = detect_chords(instrument_stem)
            print(f"[pipeline]   -> {len(chord_segments)} chord segment(s) detected")
        except ImportError:
            print("[pipeline]   -> madmom not installed, skipping chord detection "
                  "(pip install madmom-prebuilt -- see README for a one-time setup fix "
                  "its packaging needs). Tab generation continues without chord info.")
        except Exception as exc:  # noqa: BLE001 -- chords are optional; never fail the run
            print(f"[pipeline]   -> chord detection failed ({type(exc).__name__}: {exc}); "
                  f"continuing without chord info.")

    # -------------------------------------------------------------- vocal melody
    melody_notes = None
    if vocal_melody and vocals_stem is not None:
        report("melody", "Stage 4: extracting vocal melody (Basic Pitch on the "
               "vocals stem, reduced to a monophonic line)...")
        melody_transcription = transcribe(vocals_stem, song_dir / "midi_vocals", debug=debug)
        raw_vocal_notes = load_notes_from_midi(melody_transcription["midi"])
        cleaned = (merge_repeated_notes(raw_vocal_notes, max_gap=merge_gap)
                   if merge_gap is not None else raw_vocal_notes)
        melody_notes = reduce_to_melody(cleaned)
        print(f"[pipeline]   -> vocal melody: {len(raw_vocal_notes)} transcribed "
              f"note(s) reduced to {len(melody_notes)} melody note(s)")

    # ----------------------------------------------------------------- build tab
    melody_gp5_path: Optional[Path] = None
    melody_tab_txt_path: Optional[Path] = None
    if instrument == "piano":
        report("tab", "Stage 5: cleaning up notes and writing piano MIDI...")
        piano_midi_path = song_dir / f"{song_path.stem}.piano.mid"
        extra_tracks = (
            [("Vocal melody (AI transcription)", _MELODY_GM_PROGRAM, melody_notes)]
            if melody_notes else None
        )
        tab_outputs = build_piano_midi(
            midi_path,
            piano_midi_path,
            merge_gap=merge_gap,
            tempo=None,
            extra_tracks=extra_tracks,
        )
        render_input_path = piano_midi_path
        print(f"[pipeline]   -> Piano MIDI: {tab_outputs['midi']}")
    else:
        report("tab", "Stage 5: assigning frets and building .gp5 tab...")
        gp5_path = song_dir / f"{song_path.stem}.gp5"
        tab_outputs = build_gp5_from_midi(
            midi_path,
            gp5_path,
            max_fret=max_fret,
            title=song_path.stem,
            debug=debug,
            merge_gap=merge_gap,
            chord_segments=chord_segments,
        )
        render_input_path = gp5_path
        print(f"[pipeline]   -> TAB (Guitar Pro): {tab_outputs['gp5']}")
        print(f"[pipeline]   -> TAB (plain text):  {tab_outputs['tab_txt']}")
        if debug:
            print(f"[pipeline]   -> fret-assignment debug CSV: {tab_outputs['debug_csv']}")

        if melody_notes:
            melody_gp5_path = song_dir / f"{song_path.stem}.melody.gp5"
            melody_outputs = build_gp5_from_notes(
                melody_notes,
                melody_gp5_path,
                tempo=float(tab_outputs["tempo"]),  # keep both tabs on one grid
                max_fret=max_fret,
                title=f"{song_path.stem} (vocal melody)",
                merge_gap=None,  # already merged/reduced above
                track_name="Vocal melody (AI transcription)",
            )
            melody_tab_txt_path = melody_outputs["tab_txt"]
            print(f"[pipeline]   -> Vocal melody TAB: {melody_gp5_path}")

    # -------------------------------------------------------------------- render
    pdf_path = None
    if skip_render:
        report("render", "Stage 6: PDF render skipped (--skip-render)")
    else:
        report("render", "Stage 6: rendering PDF with MuseScore (optional)...")
        pdf_path = render_pdf(render_input_path)
        if pdf_path:
            print(f"[pipeline]   -> PDF: {pdf_path}")

    is_piano = instrument == "piano"
    return {
        "stems": stems,
        "midi": midi_path,
        "sonified_wav": transcribe_outputs["sonified_wav"],
        "note_events_csv": transcribe_outputs["note_events_csv"],
        "transcription_backend": transcribe_outputs["backend"],
        "gp5": None if is_piano else tab_outputs["gp5"],
        "piano_midi": tab_outputs["midi"] if is_piano else None,
        "tab_txt": None if is_piano else tab_outputs["tab_txt"],
        "debug_csv": None if is_piano else tab_outputs["debug_csv"],
        "measure_chords": [] if is_piano else tab_outputs["measure_chords"],
        "chord_mismatch_count": 0 if is_piano else tab_outputs["chord_mismatch_count"],
        "capo_suggestion": {} if is_piano else tab_outputs["capo_suggestion"],
        "melody_gp5": melody_gp5_path,
        "melody_tab_txt": melody_tab_txt_path,
        "melody_note_count": len(melody_notes) if melody_notes else 0,
        "pdf": pdf_path,
        "instrument": instrument,
        "source": source,
    }
