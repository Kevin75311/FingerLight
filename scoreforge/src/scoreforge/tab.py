"""MIDI -> guitar TAB (.gp5).

This module owns the one piece of logic that has no off-the-shelf tool: deciding
*which string and fret* to play each transcribed note on, and turning that decision
into a PyGuitarPro ``Song`` written out as a ``.gp5`` file.

The heavy lifting is split so the assignment algorithm can be unit-tested without any
MIDI/audio I/O:

    Note (dataclass)              -- one transcribed pitch with on/off times
    group_chord_events()          -- clusters notes into simultaneous "chord events"
    merge_repeated_notes()        -- PURE function: collapses oversegmented sustains
    assign_frets()                -- PURE function: notes in, string/fret assignments out
    load_notes_from_midi()        -- MIDI file -> list[Note]  (I/O)
    build_gp5_from_midi()         -- orchestrates the above and writes a .gp5 file (I/O)
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import guitarpro

from .chord import ChordSegment, chords_per_measure, flag_chord_mismatches, suggest_capo

try:
    import pretty_midi
except ImportError:  # pragma: no cover - exercised only when the dep is missing
    pretty_midi = None


# ---------------------------------------------------------------------------
# Tuning / constants
# ---------------------------------------------------------------------------

# Standard EADGBE tuning. Key = PyGuitarPro string number (1 = high E, 6 = low E),
# value = open-string MIDI pitch. This matches the string numbering PyGuitarPro/Guitar
# Pro use internally (string 1 is the thinnest/highest string).
STANDARD_TUNING: Dict[int, int] = {
    1: 64,  # E4
    2: 59,  # B3
    3: 55,  # G3
    4: 50,  # D3
    5: 45,  # A2
    6: 40,  # E2
}

DEFAULT_MAX_FRET = 19
DEFAULT_ONSET_TOLERANCE = 0.03  # seconds (~30ms), per the workflow spec

# Standard note durations we quantize to, expressed in quarter-note lengths (ql),
# largest first. 4.0 = whole note, 0.25 = sixteenth note.
_QL_STEPS: Tuple[float, ...] = (4.0, 2.0, 1.0, 0.5, 0.25)

# PyGuitarPro Duration.value for each of the steps above (1=whole ... 16=sixteenth).
_QL_TO_GP_DURATION: Dict[float, int] = {4.0: 1, 2.0: 2, 1.0: 4, 0.5: 8, 0.25: 16}

_BEATS_PER_MEASURE_QL = 4.0  # assume 4/4 time (documented simplification, see README)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    """One transcribed note. No MIDI/audio dependency baked in -- easy to construct
    synthetically for tests."""

    pitch: int  # MIDI pitch number
    start: float  # seconds
    end: float  # seconds
    velocity: int = 80


@dataclass(frozen=True)
class NoteAssignment:
    """A Note bound to a specific guitar string/fret."""

    note: Note
    string: int  # 1 (high E) .. 6 (low E)
    fret: int


# ---------------------------------------------------------------------------
# Chord-event grouping
# ---------------------------------------------------------------------------


def group_chord_events(
    notes: Sequence[Note], tolerance: float = DEFAULT_ONSET_TOLERANCE
) -> List[List[Note]]:
    """Cluster notes into "chord events" -- groups of notes that start together.

    Notes are sorted by onset time, then greedily grouped: a note joins the current
    group if its start time is within ``tolerance`` seconds of the group's anchor
    (first) note. This is a simple/heuristic clustering, not a globally optimal one --
    sufficient for a downstream pipeline that already expects manual correction.
    """
    if not notes:
        return []

    ordered = sorted(notes, key=lambda n: n.start)
    events: List[List[Note]] = [[ordered[0]]]
    for note in ordered[1:]:
        anchor_start = events[-1][0].start
        if note.start - anchor_start <= tolerance:
            events[-1].append(note)
        else:
            events.append([note])
    return events


# ---------------------------------------------------------------------------
# Fret assignment (pure function, unit-testable without MIDI I/O)
# ---------------------------------------------------------------------------


def _candidate_strings(
    pitch: int, tuning: Dict[int, int], max_fret: int
) -> List[Tuple[int, int]]:
    """All (string, fret) pairs that can produce ``pitch`` within [0, max_fret]."""
    candidates = []
    for string, open_pitch in tuning.items():
        fret = pitch - open_pitch
        if 0 <= fret <= max_fret:
            candidates.append((string, fret))
    # Prefer lower fret / lower string number first; helps the backtracking search
    # find a good-looking solution quickly (score comparison still picks the best).
    candidates.sort(key=lambda sf: (sf[1], sf[0]))
    return candidates


# Cost-model weights for fret assignment. The movement/span/position trio and the
# 100/10/1 ratio come from noahbaculi/guitar-tab-generator's pathfinding defaults
# (see docs/research/fingering-survey.md); the open-string bonus and the steep
# over-comfort-span penalty are our additions (chord-fingering-style reachability
# as a soft cost rather than a hard reject, so unplayably-wide transcribed chords
# still get *some* voicing instead of losing notes).
W_MOVEMENT = 100.0  # per fret of hand-position change between consecutive events
W_SPAN = 10.0  # per fret of span between lowest and highest fretted note in a chord
W_POSITION = 1.0  # per fret of average position (mild preference for low positions)
OPEN_STRING_BONUS = 2.0  # subtracted per open string used
MAX_COMFORT_SPAN = 4  # frets a hand can cover comfortably in one position
WIDE_SPAN_PENALTY = 250.0  # per fret of span beyond MAX_COMFORT_SPAN

# How many alternative voicings per chord event the DP considers. The optimum is
# almost always within the few tightest voicings; this caps DP cost at
# O(events * K^2).
DEFAULT_CANDIDATES_PER_EVENT = 8


@dataclass(frozen=True)
class _EventCandidate:
    """One playable voicing of a chord event: the (note, string, fret) triples,
    its intra-event playability cost, and its hand position (None = all open)."""

    assignment: Tuple[Tuple[Note, int, int], ...]
    cost: float
    center: Optional[float]


def _voicing_cost(assignment: Sequence[Tuple[Note, int, int]]) -> Tuple[float, Optional[float]]:
    """Intra-event playability cost of one voicing (lower is better) and its hand
    position (mean fretted fret, or None if every note is an open string)."""
    fretted = [fret for _, _, fret in assignment if fret > 0]
    open_count = sum(1 for _, _, fret in assignment if fret == 0)
    if not fretted:
        return (-open_count * OPEN_STRING_BONUS, None)

    span = max(fretted) - min(fretted)
    center = sum(fretted) / len(fretted)
    cost = span * W_SPAN + center * W_POSITION - open_count * OPEN_STRING_BONUS
    if span > MAX_COMFORT_SPAN:
        cost += (span - MAX_COMFORT_SPAN) * WIDE_SPAN_PENALTY
    return (cost, center)


def _enumerate_event_candidates(
    notes: Sequence[Note],
    tuning: Dict[int, int],
    max_fret: int,
    k: int = DEFAULT_CANDIDATES_PER_EVENT,
) -> List[_EventCandidate]:
    """Top-``k`` voicings for one chord event, by intra-event cost.

    Backtracking over (note -> string) choices, most-constrained-note-first. Notes
    with no playable candidate (pitch out of range) are silently dropped; if more
    notes arrive than can fit on distinct strings, the excess is dropped rather
    than raising. Only voicings placing the maximum achievable number of notes are
    returned -- note count always beats playability.
    """
    playable = [(n, _candidate_strings(n.pitch, tuning, max_fret)) for n in notes]
    playable = [(n, cands) for n, cands in playable if cands]
    if not playable:
        return []

    order = sorted(range(len(playable)), key=lambda i: len(playable[i][1]))

    complete: List[_EventCandidate] = []
    best_count = 0

    def backtrack(idx: int, used: set, current: List[Tuple[Note, int, int]]) -> None:
        nonlocal best_count
        if idx == len(order):
            count = len(current)
            if count < best_count:
                return
            cost, center = _voicing_cost(current)
            if count > best_count:
                best_count = count
                complete.clear()
            complete.append(
                _EventCandidate(assignment=tuple(current), cost=cost, center=center)
            )
            return

        note, candidates = playable[order[idx]]
        placed_any = False
        for string, fret in candidates:
            if string in used:
                continue
            placed_any = True
            current.append((note, string, fret))
            used.add(string)
            backtrack(idx + 1, used, current)
            used.discard(string)
            current.pop()

        if not placed_any:
            # No free string for this note in this branch -- skip it (drop) and
            # keep going, so one crowded note doesn't fail the whole event.
            backtrack(idx + 1, used, current)

    backtrack(0, set(), [])
    complete.sort(key=lambda c: c.cost)
    return complete[:k]


def assign_frets(
    notes: Sequence[Note],
    tuning: Optional[Dict[int, int]] = None,
    max_fret: int = DEFAULT_MAX_FRET,
    onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
    candidates_per_event: int = DEFAULT_CANDIDATES_PER_EVENT,
) -> List[NoteAssignment]:
    """Pure function: notes in, string/fret assignments out. No MIDI/audio I/O.

    Groups ``notes`` into chord events (see :func:`group_chord_events`), then finds
    the *globally* cheapest sequence of voicings by dynamic programming: each event
    contributes its top-``candidates_per_event`` voicings (by intra-event
    playability: fret span, position, open strings -- see :func:`_voicing_cost`),
    and transitions between consecutive events are charged ``W_MOVEMENT`` per fret
    of hand-position change. Guarantees per event:
      - no two notes in the same chord event share a string
      - every fret is within ``[0, max_fret]``
      - the maximum placeable number of notes is placed (count beats playability)

    All-open voicings are position-transparent: they add no movement cost and pass
    the previous hand position through, so an open string between two barre chords
    doesn't fake a trip to fret 0.

    This replaced a per-event greedy choice; the DP is optimal over the retained
    candidate set (top-K per event), which is the practical optimum -- the best
    global path essentially always runs through each event's few tightest voicings.
    """
    tuning = tuning or STANDARD_TUNING
    if not notes:
        return []

    events = group_chord_events(notes, onset_tolerance)
    event_candidates = [
        _enumerate_event_candidates(ev, tuning, max_fret, candidates_per_event)
        for ev in events
    ]
    event_candidates = [cands for cands in event_candidates if cands]
    if not event_candidates:
        return []

    # Forward DP. Each state: (total cost so far, effective hand position,
    # backpointer to the chosen state index in the previous event).
    states: List[List[Tuple[float, Optional[float], int]]] = []
    first = [(cand.cost, cand.center, -1) for cand in event_candidates[0]]
    states.append(first)

    for ev_idx in range(1, len(event_candidates)):
        prev_states = states[-1]
        current: List[Tuple[float, Optional[float], int]] = []
        for cand in event_candidates[ev_idx]:
            best_total = float("inf")
            best_pos: Optional[float] = None
            best_prev = -1
            for p_idx, (p_cost, p_pos, _) in enumerate(prev_states):
                if cand.center is None or p_pos is None:
                    movement = 0.0
                else:
                    movement = abs(cand.center - p_pos) * W_MOVEMENT
                total = p_cost + movement
                if total < best_total:
                    best_total = total
                    best_prev = p_idx
                    best_pos = cand.center if cand.center is not None else p_pos
            current.append((best_total + cand.cost, best_pos, best_prev))
        states.append(current)

    # Walk backpointers from the cheapest final state.
    chosen: List[int] = [0] * len(event_candidates)
    last_idx = min(range(len(states[-1])), key=lambda i: states[-1][i][0])
    for ev_idx in range(len(event_candidates) - 1, -1, -1):
        chosen[ev_idx] = last_idx
        last_idx = states[ev_idx][last_idx][2]

    results: List[NoteAssignment] = []
    for ev_idx, cands in enumerate(event_candidates):
        for note, string, fret in cands[chosen[ev_idx]].assignment:
            results.append(NoteAssignment(note=note, string=string, fret=fret))
    return results


# ---------------------------------------------------------------------------
# MIDI I/O
# ---------------------------------------------------------------------------


def load_notes_from_midi(midi_path: Path) -> List[Note]:
    """Load all (non-drum) notes from a MIDI file into a flat list of :class:`Note`."""
    if pretty_midi is None:  # pragma: no cover
        raise ImportError("pretty_midi is required for load_notes_from_midi(); pip install pretty_midi")

    pm = pretty_midi.PrettyMIDI(str(midi_path))
    notes: List[Note] = []
    for instrument in pm.instruments:
        if instrument.is_drum:
            continue
        for n in instrument.notes:
            notes.append(Note(pitch=n.pitch, start=n.start, end=n.end, velocity=n.velocity))
    return notes


DEFAULT_MERGE_GAP = 0.05  # seconds


def merge_repeated_notes(
    notes: Sequence[Note], max_gap: float = DEFAULT_MERGE_GAP
) -> List[Note]:
    """Merge consecutive same-pitch notes separated by a near-zero gap into one
    sustained note.

    Basic Pitch's onset detector can re-trigger spurious new onsets partway through a
    single sustained/ringing tone -- especially low notes, which have more harmonic
    complexity for the onset detector to latch onto -- producing several short notes
    back-to-back at the same pitch instead of one long one. This is a real, confirmed
    failure mode here: bass-string notes were observed starting at *exactly* the prior
    note's end time (zero gap), which is the signature of an oversegmented sustain, not
    a deliberately re-picked note (a real repeat leaves an audible attack gap). Grouping
    by exact pitch first (rather than just checking global note order) means an
    interleaved different-pitch note in between doesn't block the merge.

    Pure function, MIDI-free, unit-testable.
    """
    if not notes:
        return []

    by_pitch: Dict[int, List[Note]] = {}
    for note in notes:
        by_pitch.setdefault(note.pitch, []).append(note)

    merged: List[Note] = []
    for pitch, group in by_pitch.items():
        group.sort(key=lambda n: n.start)
        current = group[0]
        for note in group[1:]:
            gap = note.start - current.end
            if gap <= max_gap:
                current = Note(
                    pitch=pitch,
                    start=current.start,
                    end=max(current.end, note.end),
                    velocity=current.velocity,
                )
            else:
                merged.append(current)
                current = note
        merged.append(current)

    merged.sort(key=lambda n: n.start)
    return merged


# Typical song-tempo range used to correct octave errors (see correct_tempo_octave).
# Chosen wide enough to not clip genuinely fast/slow songs, narrow enough to catch the
# classic 2x/0.5x autocorrelation-doubling failure mode.
REASONABLE_TEMPO_RANGE: Tuple[float, float] = (60.0, 160.0)


def correct_tempo_octave(
    tempo: float, reasonable_range: Tuple[float, float] = REASONABLE_TEMPO_RANGE
) -> float:
    """Halve/double ``tempo`` until it falls within ``reasonable_range``.

    Autocorrelation-based tempo estimators (including pretty_midi's
    ``estimate_tempo()``) are prone to octave errors -- locking onto exactly 2x or 0.5x
    the real tempo (e.g. reporting 199 BPM for a song that's actually ~100 BPM). This is
    a pure function so the correction is unit-testable without any MIDI I/O.
    """
    low, high = reasonable_range
    if tempo <= 0:
        return tempo
    while tempo > high:
        tempo /= 2.0
    while tempo < low:
        tempo *= 2.0
    return tempo


def _estimate_tempo(midi_path: Path) -> float:
    try:
        pm = pretty_midi.PrettyMIDI(str(midi_path))
        raw_tempo = pm.estimate_tempo()
        if raw_tempo and raw_tempo > 0:
            corrected = correct_tempo_octave(float(raw_tempo))
            if abs(corrected - raw_tempo) > 1e-6:
                print(f"[tab] pretty_midi's raw tempo estimate was {raw_tempo:.1f} BPM, "
                      f"outside the typical {REASONABLE_TEMPO_RANGE[0]:.0f}-"
                      f"{REASONABLE_TEMPO_RANGE[1]:.0f} BPM range (classic octave-error "
                      f"failure mode) -- corrected to {corrected:.1f} BPM.")
            return corrected
    except Exception:
        pass
    return 120.0


# ---------------------------------------------------------------------------
# .gp5 construction
# ---------------------------------------------------------------------------


def _group_assignments_by_event(
    assignments: Sequence[NoteAssignment], tolerance: float = DEFAULT_ONSET_TOLERANCE
) -> List[Tuple[float, float, List[NoteAssignment]]]:
    """Re-group a flat assignment list back into (start, end, assignments) events,
    using the same onset-clustering rule as group_chord_events()."""
    if not assignments:
        return []

    ordered = sorted(assignments, key=lambda a: a.note.start)
    events: List[List[NoteAssignment]] = [[ordered[0]]]
    for a in ordered[1:]:
        anchor_start = events[-1][0].note.start
        if a.note.start - anchor_start <= tolerance:
            events[-1].append(a)
        else:
            events.append([a])

    result = []
    for ev in events:
        start = min(a.note.start for a in ev)
        end = max(a.note.end for a in ev)
        result.append((start, end, ev))
    return result


def _quantize_note_ql(amount: float) -> float:
    """Snap a quarter-length duration to the nearest standard note value."""
    amount = max(amount, _QL_STEPS[-1])
    return min(_QL_STEPS, key=lambda s: abs(s - amount))


def _decompose_ql(amount: float) -> List[float]:
    """Greedily decompose a quarter-length amount into standard note values
    (largest-first), used to fill gaps between chord events with rests."""
    out: List[float] = []
    remaining = round(amount, 4)
    while remaining >= _QL_STEPS[-1] - 1e-6:
        for step in _QL_STEPS:
            if remaining >= step - 1e-6:
                out.append(step)
                remaining -= step
                break
        else:  # pragma: no cover - unreachable given the while condition
            break
    return out


def _make_rest_beat(voice: "guitarpro.Voice", gp_value: int) -> "guitarpro.Beat":
    beat = guitarpro.Beat(voice=voice, status=guitarpro.BeatStatus.rest)
    beat.duration = guitarpro.Duration(value=gp_value)
    return beat


def _make_chord_beat(
    voice: "guitarpro.Voice", assignment: List[NoteAssignment], gp_value: int
) -> "guitarpro.Beat":
    beat = guitarpro.Beat(voice=voice, status=guitarpro.BeatStatus.normal)
    beat.duration = guitarpro.Duration(value=gp_value)
    for na in assignment:
        # Guitar Pro has no separate "dynamic" field -- Note.velocity IS the dynamic
        # marking (16-unit bands: pp/p/mp/mf/f/...). Basic Pitch's transcribed
        # velocities fluctuate note-to-note, which turned into a distracting dynamic
        # marking on nearly every note. Use one constant velocity so the score gets a
        # single implied dynamic instead -- we don't trust transcribed velocity as
        # meaningful dynamics data anyway.
        note = guitarpro.Note(
            beat=beat, value=na.fret, string=na.string, velocity=guitarpro.Velocities.default
        )
        beat.notes.append(note)
    return beat


def _build_timeline(
    events: List[Tuple[float, float, List[NoteAssignment]]], quarters_per_second: float
) -> List[Tuple[float, Optional[List[NoteAssignment]]]]:
    """Turn chord events into a flat (duration_ql, assignment_or_None) beat list,
    with rests filling any gaps. ``None`` assignment means a rest beat."""
    timeline: List[Tuple[float, Optional[List[NoteAssignment]]]] = []
    position_ql = 0.0

    for i, (start, end, assignment) in enumerate(events):
        event_start_ql = start * quarters_per_second
        gap = event_start_ql - position_ql
        if gap > _QL_STEPS[-1] / 2:
            for rest_dur in _decompose_ql(gap):
                timeline.append((rest_dur, None))
                position_ql += rest_dur
        elif gap < 0:
            # overlaps the previous beat (dense/noisy transcription) -- just
            # continue from where we are rather than going backwards.
            pass

        # Duration: min of (note length, gap to next event), quantized.
        note_len_sec = max(end - start, 0.05)
        note_len_ql = note_len_sec * quarters_per_second
        if i + 1 < len(events):
            next_start_ql = events[i + 1][0] * quarters_per_second
            note_len_ql = min(note_len_ql, max(next_start_ql - position_ql, _QL_STEPS[-1]))
        duration_ql = _quantize_note_ql(note_len_ql)

        timeline.append((duration_ql, assignment))
        position_ql += duration_ql

    return timeline


def _timeline_to_measures(
    timeline: List[Tuple[float, Optional[List[NoteAssignment]]]]
) -> List[List[Tuple[float, Optional[List[NoteAssignment]]]]]:
    """Pack a flat beat timeline into 4/4 measures, padding the tail of each
    measure with rests when a beat would overflow it."""
    measures: List[List[Tuple[float, Optional[List[NoteAssignment]]]]] = [[]]
    remaining = _BEATS_PER_MEASURE_QL

    for duration_ql, assignment in timeline:
        if duration_ql > remaining + 1e-6:
            # Doesn't fit -- pad out the current measure with rests, start a new one.
            for pad in _decompose_ql(remaining):
                measures[-1].append((pad, None))
            measures.append([])
            remaining = _BEATS_PER_MEASURE_QL

        measures[-1].append((duration_ql, assignment))
        remaining -= duration_ql
        if remaining <= 1e-6:
            measures.append([])
            remaining = _BEATS_PER_MEASURE_QL

    if not measures[-1]:
        measures.pop()
    elif remaining > 1e-6:
        for pad in _decompose_ql(remaining):
            measures[-1].append((pad, None))

    if not measures:
        measures = [[(_BEATS_PER_MEASURE_QL, None)]]

    return measures


def build_gp5_song(
    assignments: Sequence[NoteAssignment],
    tempo: float = 120.0,
    title: str = "Untitled",
    onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
    track_name: str = "Guitar (AI transcription)",
) -> "guitarpro.Song":
    """Build a PyGuitarPro Song (one guitar track, standard tuning) from note
    assignments, quantized to a simple 4/4 grid. This is a deliberately simple
    rhythm model (see README) -- pitch/fret accuracy matters far more than exact
    rhythmic notation for a pipeline that expects manual correction."""
    song = guitarpro.Song()
    song.title = title
    song.tempo = int(round(tempo))

    track = song.tracks[0]
    track.name = track_name
    track.channel.instrument = 25  # Acoustic Guitar (steel)
    track.strings = [
        guitarpro.GuitarString(number=s, value=p) for s, p in STANDARD_TUNING.items()
    ]

    events = _group_assignments_by_event(assignments, onset_tolerance)
    quarters_per_second = tempo / 60.0
    timeline = _build_timeline(events, quarters_per_second)
    measures_beats = _timeline_to_measures(timeline)

    n_measures = max(len(measures_beats), 1)

    # song starts with one measure header/measure per track already present.
    for m_num in range(2, n_measures + 1):
        header = guitarpro.MeasureHeader()
        header.number = m_num
        song.measureHeaders.append(header)
        for t in song.tracks:
            t.measures.append(guitarpro.Measure(t, header))

    for m_num in range(n_measures):
        measure = track.measures[m_num]
        voice = measure.voices[0]
        voice.beats.clear()
        beats = measures_beats[m_num] if m_num < len(measures_beats) else [(4.0, None)]
        for duration_ql, assignment in beats:
            gp_value = _QL_TO_GP_DURATION[duration_ql]
            if assignment:
                voice.beats.append(_make_chord_beat(voice, assignment, gp_value))
            else:
                voice.beats.append(_make_rest_beat(voice, gp_value))

    return song


_STRING_LABELS: Dict[int, str] = {1: "e", 2: "B", 3: "G", 4: "D", 5: "A", 6: "E"}


def render_ascii_tab(
    assignments: Sequence[NoteAssignment],
    onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
    events_per_line: int = 16,
) -> str:
    """Render note assignments as plain-text 6-line guitar tab (high e on top).

    This exists as a MuseScore-independent guarantee of an actual tab view: MuseScore's
    CLI/import does not reliably honor the .gp5 tablature flag (confirmed -- converting
    our own .gp5 through `MuseScore4.exe -o out.musicxml` drops the tab staff entirely),
    so a manual "enable Tablature" step in MuseScore's Instruments panel is required for
    that path. This text rendering needs no such step.
    """
    events = _group_assignments_by_event(assignments, onset_tolerance)
    if not events:
        return "(no notes)\n"

    columns: List[Dict[int, str]] = []
    for _, _, assignment in events:
        by_string = {na.string: str(na.fret) for na in assignment}
        columns.append({s: by_string.get(s, "-") for s in range(1, 7)})

    col_widths = [max(len(v) for v in col.values()) for col in columns]

    lines_out: List[str] = []
    for start in range(0, len(columns), events_per_line):
        chunk_cols = columns[start : start + events_per_line]
        chunk_widths = col_widths[start : start + events_per_line]
        for s in range(1, 7):
            row = f"{_STRING_LABELS[s]}|"
            for col, w in zip(chunk_cols, chunk_widths):
                row += col[s].rjust(w, "-") + "-"
            lines_out.append(row)
        lines_out.append("")

    return "\n".join(lines_out).rstrip() + "\n"


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _pitch_name(pitch: int) -> str:
    return f"{_NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def _dropped_notes(notes: Sequence[Note], assignments: Sequence[NoteAssignment]) -> List[Note]:
    """Notes present in ``notes`` with no corresponding entry in ``assignments``
    (out-of-range pitch, or crowded-out of a chord event). Multiset-aware so exact
    duplicate notes aren't miscounted."""
    remaining = Counter(a.note for a in assignments)
    dropped = []
    for note in notes:
        if remaining[note] > 0:
            remaining[note] -= 1
        else:
            dropped.append(note)
    return dropped


def measures_from_assignments(
    assignments: Sequence[NoteAssignment], tempo: float, onset_tolerance: float = DEFAULT_ONSET_TOLERANCE
) -> List[List[Tuple[float, Optional[List[NoteAssignment]]]]]:
    """Shared quantization walk (events -> timeline -> measures) used by both
    write_debug_csv() and the chord/measure cross-check in build_gp5_from_midi(), so
    the two stay consistent about what "measure N" means."""
    events = _group_assignments_by_event(assignments, onset_tolerance)
    quarters_per_second = tempo / 60.0
    timeline = _build_timeline(events, quarters_per_second)
    return _timeline_to_measures(timeline)


def pitch_classes_by_measure(
    assignments: Sequence[NoteAssignment], tempo: float, onset_tolerance: float = DEFAULT_ONSET_TOLERANCE
) -> List[Set[int]]:
    """Pure function: the set of distinct pitch classes (0-11) played in each measure,
    in the same measure numbering as measures_from_assignments(). Used to cross-check
    transcribed notes against an independently-detected chord for that measure."""
    measures = measures_from_assignments(assignments, tempo, onset_tolerance)
    result: List[Set[int]] = []
    for measure in measures:
        pcs: Set[int] = set()
        for _, assignment in measure:
            if assignment:
                pcs.update(na.note.pitch % 12 for na in assignment)
        result.append(pcs)
    return result


def write_debug_csv(
    notes: Sequence[Note],
    assignments: Sequence[NoteAssignment],
    tempo: float,
    output_path: Path,
    onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
    measure_chords: Optional[Sequence[str]] = None,
    measure_mismatch: Optional[Sequence[bool]] = None,
) -> Path:
    """Write a row-per-note CSV exposing exactly what the fret-assignment and
    quantization stages decided: assigned string/fret (or why a note was dropped),
    and where each note landed on the quantized measure/beat grid -- useful for
    diagnosing bar-boundary drift, since it shows the actual note start time next to
    the quantized position that was computed from it. If ``measure_chords`` is given,
    each row also gets the independently-detected chord for its measure and whether
    that measure was flagged as a possible transcription-error candidate."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    measures = measures_from_assignments(assignments, tempo, onset_tolerance)

    def chord_for(m_idx_zero: int) -> str:
        if measure_chords is None or m_idx_zero >= len(measure_chords):
            return ""
        return measure_chords[m_idx_zero]

    def mismatch_for(m_idx_zero: int) -> object:
        if measure_mismatch is None or m_idx_zero >= len(measure_mismatch):
            return ""
        return measure_mismatch[m_idx_zero]

    rows: List[Dict[str, object]] = []
    for m_idx_zero, measure in enumerate(measures):
        position_ql = 0.0
        for duration_ql, assignment in measure:
            if assignment:
                for na in assignment:
                    rows.append(
                        {
                            "dropped": False,
                            "pitch": na.note.pitch,
                            "note_name": _pitch_name(na.note.pitch),
                            "start_sec": round(na.note.start, 4),
                            "end_sec": round(na.note.end, 4),
                            "string": na.string,
                            "fret": na.fret,
                            "measure": m_idx_zero + 1,
                            "position_in_measure_ql": round(position_ql, 4),
                            "duration_ql": duration_ql,
                            "tempo_bpm": round(tempo, 2),
                            "chord": chord_for(m_idx_zero),
                            "chord_mismatch": mismatch_for(m_idx_zero),
                        }
                    )
            position_ql += duration_ql

    for note in _dropped_notes(notes, assignments):
        rows.append(
            {
                "dropped": True,
                "pitch": note.pitch,
                "note_name": _pitch_name(note.pitch),
                "start_sec": round(note.start, 4),
                "end_sec": round(note.end, 4),
                "string": "",
                "fret": "",
                "measure": "",
                "position_in_measure_ql": "",
                "duration_ql": "",
                "tempo_bpm": round(tempo, 2),
                "chord": "",
                "chord_mismatch": "",
            }
        )

    rows.sort(key=lambda r: r["start_sec"])

    fieldnames = [
        "dropped", "pitch", "note_name", "start_sec", "end_sec", "string", "fret",
        "measure", "position_in_measure_ql", "duration_ql", "tempo_bpm", "chord",
        "chord_mismatch",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def build_gp5_from_midi(
    midi_path: Path,
    output_path: Path,
    max_fret: int = DEFAULT_MAX_FRET,
    onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
    tempo: Optional[float] = None,
    title: Optional[str] = None,
    write_ascii_tab: bool = True,
    debug: bool = False,
    merge_gap: Optional[float] = DEFAULT_MERGE_GAP,
    chord_segments: Optional[Sequence[ChordSegment]] = None,
    track_name: str = "Guitar (AI transcription)",
) -> Dict[str, object]:
    """Full MIDI -> .gp5 (+ optional .tab.txt / debug.csv) path: load notes, merge
    oversegmented sustains, assign frets, build song, write files.

    ``merge_gap`` controls :func:`merge_repeated_notes` (see its docstring for why this
    matters -- confirmed fix for Basic Pitch splitting one sustained bass note into
    several repeated short ones). Pass ``None`` to disable merging entirely.

    ``chord_segments``, if given (from :func:`scoreforge.chord.detect_chords`,
    run on the same guitar-stem audio -- independent of this module's own MIDI-based
    transcription), are mapped one-per-measure and: (1) written as text above the first
    beat of each measure in the .gp5, (2) prepended as a chord chart in the .tab.txt,
    (3) cross-checked against each measure's transcribed notes to flag measures whose
    notes mostly don't fit the independently-detected chord -- a candidate
    transcription-error signal, not a certainty (see chord.flag_chord_mismatches).

    When ``debug=True``, also writes ``<output_path>.debug.csv`` -- one row per note
    (assigned string/fret + quantized measure/beat position, or the reason it was
    dropped) -- for diagnosing fret-assignment and quantization/tempo issues without
    guessing from the rendered PDF alone.

    Returns {"gp5": Path, "tab_txt": Optional[Path], "debug_csv": Optional[Path],
    "tempo": float, "note_count": int, "dropped_count": int, "merged_count": int,
    "measure_chords": List[str], "chord_mismatch_count": int, "capo_suggestion": dict}.
    """
    midi_path = Path(midi_path)
    raw_notes = load_notes_from_midi(midi_path)
    if not raw_notes:
        print(f"[tab] Warning: no notes found in {midi_path}; writing an empty .gp5")

    resolved_tempo = tempo if tempo is not None else _estimate_tempo(midi_path)
    print(f"[tab] Using tempo: {resolved_tempo:.1f} BPM "
          f"({'explicit' if tempo is not None else 'estimated from MIDI'}), 4/4 time "
          f"(fixed assumption -- see README's quantization note)")

    return build_gp5_from_notes(
        raw_notes,
        output_path,
        tempo=resolved_tempo,
        max_fret=max_fret,
        onset_tolerance=onset_tolerance,
        title=title or midi_path.stem,
        write_ascii_tab=write_ascii_tab,
        debug=debug,
        merge_gap=merge_gap,
        chord_segments=chord_segments,
        track_name=track_name,
    )


def build_gp5_from_notes(
    raw_notes: Sequence[Note],
    output_path: Path,
    tempo: float,
    max_fret: int = DEFAULT_MAX_FRET,
    onset_tolerance: float = DEFAULT_ONSET_TOLERANCE,
    title: str = "Untitled",
    write_ascii_tab: bool = True,
    debug: bool = False,
    merge_gap: Optional[float] = DEFAULT_MERGE_GAP,
    chord_segments: Optional[Sequence[ChordSegment]] = None,
    track_name: str = "Guitar (AI transcription)",
) -> Dict[str, object]:
    """Notes -> .gp5 (+ optional .tab.txt / debug.csv), same behavior as
    :func:`build_gp5_from_midi` but starting from in-memory :class:`Note` objects
    (already-loaded MIDI, or a vocal melody line from :mod:`melody`) with an
    explicit ``tempo``. Same return dict as :func:`build_gp5_from_midi`.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if merge_gap is not None:
        notes = merge_repeated_notes(list(raw_notes), max_gap=merge_gap)
        merged_count = len(raw_notes) - len(notes)
        if merged_count > 0:
            print(f"[tab] Merged {merged_count} oversegmented-sustain note(s) "
                  f"(same pitch, <= {merge_gap * 1000:.0f}ms gap) into longer notes: "
                  f"{len(raw_notes)} -> {len(notes)} notes.")
    else:
        notes = list(raw_notes)
        merged_count = 0

    assignments = assign_frets(notes, max_fret=max_fret, onset_tolerance=onset_tolerance)

    dropped_count = len(notes) - len(assignments)
    if dropped_count > 0:
        print(f"[tab] Warning: {dropped_count} note(s) could not be placed on the fretboard "
              f"(out of range or too many simultaneous notes) and were dropped.")

    resolved_tempo = tempo

    song = build_gp5_song(
        assignments,
        tempo=resolved_tempo,
        title=title,
        onset_tolerance=onset_tolerance,
        track_name=track_name,
    )

    n_measures = len(song.tracks[0].measures)
    measure_chords: List[str] = chords_per_measure(chord_segments or [], resolved_tempo, n_measures)
    mismatch_count = 0
    capo_suggestion: Dict[str, object] = {}
    if chord_segments:
        pcs_by_measure = pitch_classes_by_measure(assignments, resolved_tempo, onset_tolerance)
        mismatch_flags = flag_chord_mismatches(measure_chords, pcs_by_measure)
        mismatch_count = sum(mismatch_flags)
        capo_suggestion = suggest_capo(measure_chords)
        print(f"[tab] Chords: {len(chord_segments)} segment(s) detected, mapped to "
              f"{n_measures} measures. {mismatch_count} measure(s) flagged where "
              f"transcribed notes don't fit the detected chord well (worth a second "
              f"look, not necessarily wrong).")
        if capo_suggestion.get("total"):
            print(f"[tab] Suggested capo: fret {capo_suggestion['capo']} -> "
                  f"{capo_suggestion['open_shape_count']}/{capo_suggestion['total']} "
                  f"chords become open shapes (heuristic, not the recording's actual capo).")

        for m_idx_zero, (chord_label, mismatched) in enumerate(zip(measure_chords, mismatch_flags)):
            if chord_label == "N":
                continue
            beats = song.tracks[0].measures[m_idx_zero].voices[0].beats
            if beats:
                beats[0].text = f"{chord_label}{' ?' if mismatched else ''}"
    else:
        mismatch_flags = [False] * n_measures

    guitarpro.write(song, str(output_path))

    tab_txt_path: Optional[Path] = None
    if write_ascii_tab:
        tab_txt_path = output_path.with_suffix(".tab.txt")
        tab_text = render_ascii_tab(assignments, onset_tolerance)
        if chord_segments:
            chart_lines = ["Chords by measure (madmom, independent of note transcription):"]
            for m_idx_zero, (chord_label, mismatched) in enumerate(zip(measure_chords, mismatch_flags)):
                if chord_label == "N":
                    continue
                flag = "  <- notes don't fit well, check this measure" if mismatched else ""
                chart_lines.append(f"  measure {m_idx_zero + 1:>3}: {chord_label}{flag}")
            if capo_suggestion.get("total"):
                chart_lines.append(
                    f"  suggested capo: fret {capo_suggestion['capo']} "
                    f"({capo_suggestion['open_shape_count']}/{capo_suggestion['total']} "
                    f"chords become open shapes)"
                )
            tab_text = "\n".join(chart_lines) + "\n\n" + tab_text
        tab_txt_path.write_text(tab_text, encoding="utf-8")

    debug_csv_path: Optional[Path] = None
    if debug:
        debug_csv_path = output_path.with_suffix(".debug.csv")
        write_debug_csv(
            notes, assignments, resolved_tempo, debug_csv_path, onset_tolerance,
            measure_chords=measure_chords if chord_segments else None,
            measure_mismatch=mismatch_flags if chord_segments else None,
        )

    return {
        "gp5": output_path,
        "tab_txt": tab_txt_path,
        "debug_csv": debug_csv_path,
        "tempo": resolved_tempo,
        "note_count": len(notes),
        "dropped_count": dropped_count,
        "merged_count": merged_count,
        "measure_chords": measure_chords,
        "chord_mismatch_count": mismatch_count,
        "capo_suggestion": capo_suggestion,
    }


def build_piano_midi(
    midi_path: Path,
    output_path: Path,
    merge_gap: Optional[float] = DEFAULT_MERGE_GAP,
    tempo: Optional[float] = None,
    program: int = 0,  # GM program 0 = Acoustic Grand Piano
    extra_tracks: Optional[Sequence[Tuple[str, int, Sequence[Note]]]] = None,
) -> Dict[str, object]:
    """MIDI -> cleaned-up piano MIDI path: load notes, merge oversegmented sustains,
    write a fresh single-instrument (piano) standard MIDI file.

    Unlike :func:`build_gp5_from_midi`, this skips fret assignment entirely -- there's
    no clean way to represent absolute-pitch piano notation in PyGuitarPro's
    string+fret ``Note`` model, so piano mode instead hands MuseScore a plain MIDI
    file, which it can import directly into grand-staff notation for the PDF render
    (see render.py/pipeline.py).

    ``merge_gap`` controls :func:`merge_repeated_notes`, same as ``build_gp5_from_midi``
    -- pass ``None`` to disable merging entirely.

    ``extra_tracks``: optional additional (track_name, GM_program, notes) tracks --
    e.g. an extracted vocal melody line (see :mod:`melody`) -- written as separate
    instruments in the same MIDI file, so MuseScore imports them as separate staves
    above/below the piano's grand staff.

    Returns {"midi": Path, "tempo": float, "note_count": int, "merged_count": int}.
    """
    if pretty_midi is None:  # pragma: no cover
        raise ImportError("pretty_midi is required for build_piano_midi(); pip install pretty_midi")

    midi_path = Path(midi_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_notes = load_notes_from_midi(midi_path)
    if not raw_notes:
        print(f"[tab] Warning: no notes found in {midi_path}; writing an empty piano MIDI")

    if merge_gap is not None:
        notes = merge_repeated_notes(raw_notes, max_gap=merge_gap)
        merged_count = len(raw_notes) - len(notes)
        if merged_count > 0:
            print(f"[tab] Merged {merged_count} oversegmented-sustain note(s) "
                  f"(same pitch, <= {merge_gap * 1000:.0f}ms gap) into longer notes: "
                  f"{len(raw_notes)} -> {len(notes)} notes.")
    else:
        notes = raw_notes
        merged_count = 0

    resolved_tempo = tempo if tempo is not None else _estimate_tempo(midi_path)
    print(f"[tab] Using tempo: {resolved_tempo:.1f} BPM "
          f"({'explicit' if tempo is not None else 'estimated from MIDI'})")

    pm = pretty_midi.PrettyMIDI(initial_tempo=resolved_tempo)
    instrument = pretty_midi.Instrument(program=program, name="Piano (AI transcription)")
    for note in notes:
        instrument.notes.append(
            pretty_midi.Note(velocity=note.velocity, pitch=note.pitch, start=note.start, end=note.end)
        )
    pm.instruments.append(instrument)

    for track_name, track_program, track_notes in extra_tracks or []:
        extra = pretty_midi.Instrument(program=track_program, name=track_name)
        for note in track_notes:
            extra.notes.append(
                pretty_midi.Note(velocity=note.velocity, pitch=note.pitch, start=note.start, end=note.end)
            )
        pm.instruments.append(extra)

    pm.write(str(output_path))
    print(f"[tab] Wrote piano MIDI ({len(notes)} note(s)"
          + (f" + {len(extra_tracks)} extra track(s)" if extra_tracks else "")
          + f"): {output_path}")

    return {
        "midi": output_path,
        "tempo": resolved_tempo,
        "note_count": len(notes),
        "merged_count": merged_count,
    }
