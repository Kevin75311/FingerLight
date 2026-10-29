"""Chord recognition via madmom -- audio-based, INDEPENDENT of Basic Pitch's MIDI
transcription. That independence is the whole point: it lets a detected chord serve as
a cross-check on our note-by-note transcription (see flag_chord_mismatches()), which a
chord derived from the same notes we're trying to validate couldn't do.

madmom's own PyPI release (0.16.1, 2018) doesn't build on Windows/Python 3.11 -- long-
standing, unresolved upstream Cython/MSVC issues (github.com/CPJKU/madmom issues #145,
#523). We install the third-party `madmom-prebuilt` wheel instead. That wheel has its
own packaging bug: its dist-info folder is named `madmom_prebuilt-*`, but
`madmom/__init__.py` looks itself up as `importlib.metadata.distribution("madmom")` and
crashes with PackageNotFoundError if that exact name isn't registered. Fix (one-time,
per env -- see README): duplicate the dist-info folder as `madmom-<version>.dist-info`
with `Name: madmom` in its METADATA.

Chord vocabulary: major/minor triads + "N" (no chord) only -- no 7ths/sus/dim/etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

_PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_NAME_TO_PC: Dict[str, int] = {name: i for i, name in enumerate(_PITCH_CLASS_NAMES)}


@dataclass(frozen=True)
class ChordSegment:
    """One detected chord region. ``label`` is madmom's own format, e.g. "D#:maj",
    "C:min", or "N" (no chord detected)."""

    start: float
    end: float
    label: str


def detect_chords(audio_path: Path) -> List[ChordSegment]:
    """Run madmom's deep-chroma chord recognizer on an audio file. Raises ImportError
    if madmom isn't installed -- callers should catch this and treat chord detection
    as an optional stage (mirrors how render.py soft-skips when MuseScore isn't found)."""
    from madmom.audio.chroma import DeepChromaProcessor
    from madmom.features.chords import DeepChromaChordRecognitionProcessor

    dcp = DeepChromaProcessor()
    decode = DeepChromaChordRecognitionProcessor()
    chroma = dcp(str(audio_path))
    raw = decode(chroma)
    return [ChordSegment(start=float(s), end=float(e), label=str(label)) for s, e, label in raw]


def parse_chord_label(label: str) -> Optional[Tuple[int, str]]:
    """"D#:maj" -> (3, "maj"); "N" or an unrecognized label -> None. Pure function."""
    if label == "N" or ":" not in label:
        return None
    root_name, quality = label.split(":", 1)
    root_pc = _NAME_TO_PC.get(root_name)
    if root_pc is None:
        return None
    return root_pc, quality


_TRIAD_INTERVALS: Dict[str, Tuple[int, ...]] = {"maj": (0, 4, 7), "min": (0, 3, 7)}


def expected_pitch_classes(root_pc: int, quality: str) -> Set[int]:
    """Pitch classes (0-11) of the triad for ``quality`` (defaults to major intervals
    for any quality outside madmom's maj/min vocabulary). Pure function."""
    intervals = _TRIAD_INTERVALS.get(quality, _TRIAD_INTERVALS["maj"])
    return {(root_pc + iv) % 12 for iv in intervals}


def chords_per_measure(
    segments: Sequence[ChordSegment],
    tempo: float,
    n_measures: int,
    beats_per_measure: float = 4.0,
) -> List[str]:
    """Majority-overlap vote: for each measure's [start, end) time window (derived
    from ``tempo`` assuming fixed 4/4 -- the same assumption tab.py's own quantization
    already makes, so measure numbers line up), pick whichever chord segment covers the
    most of that window. Pure function, no I/O.
    """
    if n_measures <= 0:
        return []
    if not segments or tempo <= 0:
        return ["N"] * n_measures

    seconds_per_measure = beats_per_measure * 60.0 / tempo
    labels: List[str] = []
    for m in range(n_measures):
        m_start = m * seconds_per_measure
        m_end = (m + 1) * seconds_per_measure
        overlap_by_label: Dict[str, float] = {}
        for seg in segments:
            overlap = min(seg.end, m_end) - max(seg.start, m_start)
            if overlap > 0:
                overlap_by_label[seg.label] = overlap_by_label.get(seg.label, 0.0) + overlap
        labels.append(max(overlap_by_label, key=overlap_by_label.get) if overlap_by_label else "N")
    return labels


def flag_chord_mismatches(
    measure_chords: Sequence[str],
    measure_pitch_classes: Sequence[Set[int]],
    min_match_fraction: float = 0.5,
) -> List[bool]:
    """For each measure, True if the transcribed notes' pitch classes mostly don't fit
    the independently-detected chord -- a candidate transcription error, NOT a
    certainty (our chord vocabulary is triads-only, and real playing legitimately
    includes passing tones/melody notes outside the chord; treat this as "worth a
    second look", not "definitely wrong"). Pure function.
    """
    flags: List[bool] = []
    for chord_label, pcs in zip(measure_chords, measure_pitch_classes):
        parsed = parse_chord_label(chord_label)
        if parsed is None or not pcs:
            flags.append(False)
            continue
        root_pc, quality = parsed
        expected = expected_pitch_classes(root_pc, quality)
        match_fraction = sum(1 for pc in pcs if pc in expected) / len(pcs)
        flags.append(match_fraction < min_match_fraction)
    return flags


# "Easy"/open-position chord shapes playable without a barre, keyed by (root_pc, quality).
_OPEN_SHAPES: Set[Tuple[int, str]] = {
    (_NAME_TO_PC[root], quality)
    for root, quality in [
        ("E", "maj"), ("A", "maj"), ("D", "maj"), ("G", "maj"), ("C", "maj"),
        ("E", "min"), ("A", "min"), ("D", "min"),
    ]
}


def suggest_capo(measure_chords: Sequence[str], max_capo: int = 7) -> Dict[str, object]:
    """Try capo positions 0..max_capo; for each, transpose every detected chord DOWN
    by the capo fret (capo raises pitch, so the shape you finger is the chord
    transposed down by the capo position) and count how many land on an open/easy
    shape. Returns the best-scoring capo position and the resulting shapes to play.

    This is a fingering-convenience heuristic, not a claim about what capo (if any)
    the original recording used -- the same audible chords are reachable via different
    capo/shape combinations. Pure function.
    """
    parsed = [p for p in (parse_chord_label(c) for c in measure_chords) if p is not None]
    if not parsed:
        return {"capo": 0, "open_shape_count": 0, "total": 0, "shapes": []}

    best_capo, best_count = 0, -1
    for capo in range(max_capo + 1):
        count = sum(
            1 for root_pc, quality in parsed if ((root_pc - capo) % 12, quality) in _OPEN_SHAPES
        )
        if count > best_count:
            best_capo, best_count = capo, count

    shapes = [
        f"{_PITCH_CLASS_NAMES[(root_pc - best_capo) % 12]}{'m' if quality == 'min' else ''}"
        for root_pc, quality in parsed
    ]
    return {"capo": best_capo, "open_shape_count": best_count, "total": len(parsed), "shapes": shapes}
