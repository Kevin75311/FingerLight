"""MusicXML ingest (Part 3, project item A3): read a MusicXML score into this
project's :class:`~fingerlight_score.notes.Note` model.

Public surface::

    from fingerlight_score.musicxml import parse_musicxml, musicxml_to_lightscore

Parses via ``defusedxml.ElementTree`` (scores arrive from the internet; the stdlib
``xml.etree`` parser is vulnerable to entity-expansion attacks) and falls back to
``xml.etree.ElementTree`` only if ``defusedxml`` cannot be imported at all.

Cursor model
------------
MusicXML expresses time as a single running "divisions" cursor per part, shared by
every voice/staff: a plain ``<note>`` advances it by its ``<duration>``, ``<chord/>``
notes share the *previous* note's onset without advancing it, and ``<backup>`` /
``<forward>`` rewind or fast-forward it explicitly (this is how a grand staff writes
its second voice -- see ``fixtures/README.md``). The cursor is **not** reset at
measure boundaries: for a well-formed score every voice's advances and backups within
a measure sum to the same value, so the cursor position naturally lines up at the next
measure's start. ``<divisions>`` (divisions per quarter note) is tracked as running
state and can change mid-score (trap #1 in the fixture README) -- every ``<duration>``
is converted to quarter-lengths using whichever ``<divisions>`` was active at the time,
so cross-measure accumulation stays correct even across a divisions change.

Grace notes (``<grace/>``) carry no ``<duration>`` and are **skipped** (not emitted,
cursor unaffected) -- see the docstring of ``_walk_part`` for the rationale and the
``warnings`` entry this produces.
"""

from __future__ import annotations

import zipfile
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import defusedxml.ElementTree as ET  # type: ignore[import]

    _USING_DEFUSEDXML = True
except ImportError:  # pragma: no cover - environment without defusedxml
    import xml.etree.ElementTree as ET  # type: ignore[import]

    _USING_DEFUSEDXML = False

from .emit import to_lightscore
from .fingering import assign_fingering
from .notes import Note

MIN_MIDI_PITCH = 21
MAX_MIDI_PITCH = 108
DEFAULT_BPM = 120.0

_STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


@dataclass
class MusicXmlScore:
    """Result of :func:`parse_musicxml`."""

    notes: List[Note]
    tempo_map: List[dict] = field(default_factory=list)  # [{"t": ms, "bpm": bpm}, ...]
    meta: dict = field(default_factory=dict)  # title, parts, warnings, ...


# ---------------------------------------------------------------------------
# Container / XML loading
# ---------------------------------------------------------------------------


def _read_xml_bytes(path: str) -> bytes:
    """Return the raw ``score-partwise`` XML bytes for ``path``.

    Handles plain ``.musicxml``/``.xml`` files directly. For a compressed ``.mxl``
    container, the inner filename is **not fixed** -- ``META-INF/container.xml``
    names it via ``<rootfile full-path="...">`` (trap #10 in the fixture README) and
    must be followed rather than assumed (e.g. never hardcode ``score.xml``).
    """
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            container = ET.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.find("./rootfiles/rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise ValueError(f"{path}: META-INF/container.xml has no rootfile full-path")
            inner_name = rootfile.get("full-path")
            return zf.read(inner_name)
    with open(path, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Pitch
# ---------------------------------------------------------------------------


def _pitch_to_midi(pitch_el) -> int:
    """MIDI = (octave + 1) * 12 + step_semitone + alter.

    ``<alter>`` is a *pitch* element (may be absent, +-1, or +-2) and is independent
    of the ``<accidental>`` display element -- only ``<alter>`` is used here (trap #7).
    """
    step = pitch_el.find("step").text.strip()
    octave = int(pitch_el.find("octave").text)
    alter_el = pitch_el.find("alter")
    alter = int(round(float(alter_el.text))) if alter_el is not None and alter_el.text else 0
    return (octave + 1) * 12 + _STEP_SEMITONE[step] + alter


# ---------------------------------------------------------------------------
# Tempo map: quarter-position -> bpm checkpoints, and quarters -> ms conversion
# ---------------------------------------------------------------------------


class _TempoMap:
    """Piecewise-constant bpm over quarter-note position, built from tempo events
    recorded (in quarters-since-piece-start, bpm) as they're encountered in document
    order. Converts a quarter position to milliseconds by integrating segment by
    segment -- so a mid-score tempo change (or a tie/duration spanning one) is honoured
    rather than ignored (trap #8)."""

    def __init__(self, events: Sequence[Tuple[float, float]]):
        # Always start with an implicit 120bpm-from-0 checkpoint; real events (sorted,
        # first-at-a-given-q wins if duplicated) are layered on top.
        ordered = sorted({q: bpm for q, bpm in sorted(events, key=lambda e: e[0])}.items())
        self._qs: List[float] = [0.0]
        self._ms: List[float] = [0.0]
        self._bpm: List[float] = [DEFAULT_BPM]
        cur_bpm = DEFAULT_BPM
        for q, bpm in ordered:
            if q < self._qs[-1]:
                continue  # ignore a tempo event that would move time backward
            ms = self._ms[-1] + (q - self._qs[-1]) * 60000.0 / cur_bpm
            if q == 0.0:
                # Replace the implicit default rather than adding a duplicate q=0.
                self._qs[-1], self._ms[-1], self._bpm[-1] = 0.0, 0.0, bpm
            else:
                self._qs.append(q)
                self._ms.append(ms)
                self._bpm.append(bpm)
            cur_bpm = bpm
        self.has_explicit_tempo = bool(ordered)

    def to_ms(self, q: float) -> float:
        i = bisect_right(self._qs, q) - 1
        i = max(0, min(i, len(self._qs) - 1))
        return self._ms[i] + (q - self._qs[i]) * 60000.0 / self._bpm[i]

    def as_lightscore_tempo(self) -> List[dict]:
        return [{"t": int(round(ms)), "bpm": bpm} for ms, bpm in zip(self._ms, self._bpm)]


# ---------------------------------------------------------------------------
# Part selection
# ---------------------------------------------------------------------------


def _score_parts(root) -> List[Tuple[str, str]]:
    """[(id, name), ...] in document order from <part-list>."""
    out = []
    for sp in root.findall("./part-list/score-part"):
        pid = sp.get("id", "")
        name_el = sp.find("part-name")
        out.append((pid, name_el.text.strip() if name_el is not None and name_el.text else pid))
    return out


def _select_part(root, part: Optional[str], warnings: List[str]):
    parts_by_id = {p.get("id"): p for p in root.findall("./part")}
    score_parts = _score_parts(root)

    if part is not None:
        for pid, name in score_parts:
            if part == pid or part.strip().lower() == name.strip().lower():
                el = parts_by_id.get(pid)
                if el is None:
                    raise ValueError(f"score-part {pid!r} has no matching <part> element")
                return pid, name, el
        raise ValueError(f"no part matches part={part!r}; available: {score_parts!r}")

    candidates = [
        (pid, name, parts_by_id[pid])
        for pid, name in score_parts
        if pid in parts_by_id and parts_by_id[pid].find(".//note") is not None
    ]
    if not candidates:
        if not score_parts:
            raise ValueError("no <score-part> entries found in <part-list>")
        pid, name = score_parts[0]
        warnings.append(f"no part contains any <note>; defaulting to first part {pid!r}")
        return pid, name, parts_by_id[pid]

    chosen = candidates[0]
    ignored = [pid for pid, _, _ in candidates[1:]]
    if ignored:
        warnings.append(
            f"multiple parts contain notes; using {chosen[0]!r}, ignoring {ignored!r} "
            "(pass part=... to select a different one)"
        )
    return chosen


# ---------------------------------------------------------------------------
# Single walk over the chosen <part>: builds raw note/rest events + tempo events
# ---------------------------------------------------------------------------


@dataclass
class _RawEvent:
    pitch: Optional[int]  # None for a rest
    voice: str
    staff: int
    staff_is_explicit: bool
    q_onset: float
    q_duration: float
    tie_start: bool
    tie_stop: bool


def _has_tie(note_el, kind: str) -> bool:
    for tie_el in note_el.findall("tie"):
        if tie_el.get("type") == kind:
            return True
    for tied_el in note_el.findall("notations/tied"):
        if tied_el.get("type") == kind:
            return True
    return False


def _walk_part(part_el, warnings: List[str]) -> Tuple[List[_RawEvent], List[Tuple[float, float]]]:
    """Single document-order walk producing raw (still-in-quarters, ties unmerged)
    note/rest events and tempo checkpoints.

    Chord handling (trap #3): a non-chord note's cursor advance is deferred until we
    know whether it is immediately followed by ``<chord/>`` notes sharing its onset --
    ``pending_advance``/``group_onset`` hold that state and are flushed (cursor +=
    pending_advance) before any backup/forward/direction/attributes/new-group note.
    """
    divisions = 1.0
    cursor_q = 0.0
    pending_advance = 0.0
    group_onset = 0.0

    events: List[_RawEvent] = []
    tempo_events: List[Tuple[float, float]] = []
    staff_voices: Dict[int, set] = {}
    # One-element list so the inner walk can set it without a `nonlocal` declaration.
    saw_staff_element = [False]
    seen_grace = 0
    seen_transpose = False
    seen_repeat_or_ending = False

    def flush():
        nonlocal cursor_q, pending_advance
        cursor_q += pending_advance
        pending_advance = 0.0

    for measure in part_el.findall("measure"):
        for child in measure:
            tag = child.tag
            if tag == "attributes":
                div_el = child.find("divisions")
                if div_el is not None and div_el.text:
                    divisions = float(div_el.text)
                if child.find("transpose") is not None:
                    if not seen_transpose:
                        warnings.append(
                            "ignoring <transpose> (transposing instrument); pitches are "
                            "written concert/as-notated"
                        )
                    seen_transpose = True
            elif tag == "direction":
                flush()
                bpm = None
                sound_el = child.find("sound")
                if sound_el is not None and sound_el.get("tempo"):
                    bpm = float(sound_el.get("tempo"))
                if bpm is None:
                    pm_el = child.find("direction-type/metronome/per-minute")
                    if pm_el is not None and pm_el.text:
                        bpm = float(pm_el.text)
                if bpm is not None:
                    tempo_events.append((cursor_q, bpm))
            elif tag == "sound":
                flush()
                if child.get("tempo"):
                    tempo_events.append((cursor_q, float(child.get("tempo"))))
            elif tag == "backup":
                flush()
                dur = child.find("duration")
                cursor_q -= (int(dur.text) if dur is not None else 0) / divisions
            elif tag == "forward":
                flush()
                dur = child.find("duration")
                cursor_q += (int(dur.text) if dur is not None else 0) / divisions
            elif tag == "barline":
                if child.find("repeat") is not None or measure.find("./barline/ending") is not None:
                    seen_repeat_or_ending = True
            elif tag == "note":
                is_chord = child.find("chord") is not None
                is_grace = child.find("grace") is not None
                is_rest = child.find("rest") is not None
                dur_el = child.find("duration")
                q_dur = (int(dur_el.text) / divisions) if dur_el is not None else 0.0

                if is_grace:
                    # No <duration> to advance by; emitted as neither a note nor a
                    # cursor move. We choose to SKIP grace notes entirely (documented
                    # here and in the module docstring) rather than fabricate a
                    # duration for them.
                    seen_grace += 1
                    continue

                if not is_chord:
                    flush()
                    group_onset = cursor_q
                    pending_advance = q_dur
                else:
                    pending_advance = max(pending_advance, q_dur)

                voice_el = child.find("voice")
                voice = voice_el.text.strip() if voice_el is not None and voice_el.text else "1"
                staff_el = child.find("staff")
                if staff_el is not None and staff_el.text:
                    staff = int(staff_el.text)
                    staff_is_explicit = True
                    saw_staff_element[0] = True
                else:
                    staff = 1
                    staff_is_explicit = False
                staff_voices.setdefault(staff, set()).add(voice)

                if is_rest:
                    continue

                pitch_el = child.find("pitch")
                if pitch_el is None:
                    # <unpitched> or something we don't model; skip with a warning.
                    warnings.append("skipped a <note> with no <pitch> and no <rest> (e.g. <unpitched>)")
                    continue

                events.append(
                    _RawEvent(
                        pitch=_pitch_to_midi(pitch_el),
                        voice=voice,
                        staff=staff,
                        staff_is_explicit=staff_is_explicit,
                        q_onset=group_onset,
                        q_duration=q_dur,
                        tie_start=_has_tie(child, "start"),
                        tie_stop=_has_tie(child, "stop"),
                    )
                )
        flush()  # end of measure: land exactly at the measure's end position

    if seen_grace:
        warnings.append(f"skipped {seen_grace} grace note(s) (no <duration> in source)")
    if seen_repeat_or_ending:
        warnings.append("repeats/voltas present in source and were NOT unfolded")
    for staff, voices in sorted(staff_voices.items()):
        if len(voices) > 1:
            warnings.append(
                f"staff {staff} has multiple voices {sorted(voices)!r}; merged onto one staff"
            )
    if events and not saw_staff_element[0]:
        # Hand assignment keys off <staff> (staff 1 -> R, 2 -> L) when hand_mode=
        # "staff". A score that carries no <staff> at all -- a single-staff lead
        # sheet, a bare export, or Audiveris OMR output -- therefore lands entirely
        # on staff 1, so hand_mode="staff" would finger the whole score as one hand.
        # Nothing about that output looks malformed, which is exactly why it is
        # warned. This is the concrete case Q-09 exists to solve: every note here
        # also gets staff_is_explicit=False, so the default hand_mode="auto" (see
        # fingering.assign_fingering / hands.py) falls back to "pitch" mode instead
        # of silently mis-fingering the whole piece as one hand.
        warnings.append(
            "no <staff> elements in source; every note assigned to staff 1 with "
            "staff_is_explicit=False, so hand_mode='staff' would put the whole "
            "score in the right hand -- hand_mode='auto' (the default) falls back "
            "to pitch-based hand splitting instead"
        )

    return events, tempo_events


# ---------------------------------------------------------------------------
# Tie merging (trap #6): a tied pair is ONE note, duration = sum of the segments
# ---------------------------------------------------------------------------


def _merge_ties(
    events: List[_RawEvent], warnings: List[str]
) -> List[Tuple[int, int, float, float, bool]]:
    """Returns [(staff, pitch, q_onset, q_duration, staff_is_explicit), ...] with tied
    runs merged into one entry each. Matches ties by (pitch, voice, staff), in onset
    order, per note. ``staff_is_explicit`` is carried from the segment that opened the
    tie (all segments of one tied note share the same staff, so they share the flag
    too)."""
    open_ties: Dict[Tuple[int, str, int], List] = {}  # key -> [q_onset, q_duration, staff_is_explicit]
    merged: List[Tuple[int, int, float, float, bool]] = []

    for ev in sorted(events, key=lambda e: e.q_onset):
        key = (ev.pitch, ev.voice, ev.staff)
        if ev.tie_stop and key in open_ties:
            open_ties[key][1] += ev.q_duration
            if ev.tie_start:
                continue  # tie continues further (a "tie middle" note)
            q_onset, q_dur, staff_is_explicit = open_ties.pop(key)
            merged.append((ev.staff, ev.pitch, q_onset, q_dur, staff_is_explicit))
            continue

        if ev.tie_start:
            if key in open_ties:
                # Overlapping tie-start for the same (pitch, voice, staff) with no
                # stop in between -- flush the stale one rather than let it hang.
                q_onset, q_dur, staff_is_explicit = open_ties.pop(key)
                merged.append((key[2], key[0], q_onset, q_dur, staff_is_explicit))
                warnings.append(
                    f"tie for pitch={key[0]} voice={key[1]} staff={key[2]} was never closed "
                    "before a new tie started on the same note; flushed"
                )
            open_ties[key] = [ev.q_onset, ev.q_duration, ev.staff_is_explicit]
            continue

        merged.append((ev.staff, ev.pitch, ev.q_onset, ev.q_duration, ev.staff_is_explicit))

    for (pitch, voice, staff), (q_onset, q_dur, staff_is_explicit) in open_ties.items():
        merged.append((staff, pitch, q_onset, q_dur, staff_is_explicit))
        warnings.append(
            f"tie for pitch={pitch} voice={voice} staff={staff} never closed (<tie type=\"stop\"/> "
            "missing); flushed with its accumulated duration instead of hanging"
        )

    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_musicxml(path: str, *, part: Optional[str] = None) -> MusicXmlScore:
    """Parse a MusicXML file (``.musicxml``/``.xml``, or compressed ``.mxl``) into a
    :class:`MusicXmlScore`.

    ``part``: a ``<score-part id="...">`` id, or its ``<part-name>`` text (case-
    insensitive), to select which part to ingest when the score has more than one.
    Defaults to the first part (in ``<part-list>`` order) that contains any ``<note>``.
    """
    xml_bytes = _read_xml_bytes(path)
    root = ET.fromstring(xml_bytes)
    if root.tag != "score-partwise":
        raise ValueError(f"{path}: only <score-partwise> is supported, got <{root.tag}>")

    warnings: List[str] = []
    part_id, part_name, part_el = _select_part(root, part, warnings)

    events, tempo_events = _walk_part(part_el, warnings)
    merged = _merge_ties(events, warnings)

    tempo_map = _TempoMap(tempo_events)
    if not tempo_map.has_explicit_tempo:
        warnings.append(f"no tempo found in source; defaulting to {DEFAULT_BPM:g} bpm")

    notes: List[Note] = []
    for staff, pitch, q_onset, q_dur, staff_is_explicit in merged:
        t_ms = int(round(tempo_map.to_ms(q_onset)))
        d_ms = int(round(tempo_map.to_ms(q_onset + q_dur) - tempo_map.to_ms(q_onset)))
        if pitch < MIN_MIDI_PITCH or pitch > MAX_MIDI_PITCH:
            warnings.append(
                f"dropped pitch {pitch} at t={t_ms}ms (outside the 88-key range "
                f"{MIN_MIDI_PITCH}..{MAX_MIDI_PITCH})"
            )
            continue
        if d_ms <= 0:
            d_ms = 1  # lightscore requires d > 0; guard a degenerate zero-length note
        notes.append(
            Note(
                t_ms=t_ms,
                d_ms=d_ms,
                pitch=pitch,
                staff=staff,
                staff_is_explicit=staff_is_explicit,
            )
        )

    notes.sort(key=lambda n: (n.t_ms, n.pitch))

    work_title_el = root.find("./work/work-title")
    title = work_title_el.text.strip() if work_title_el is not None and work_title_el.text else None

    meta = {
        "title": title,
        "part_id": part_id,
        "part_name": part_name,
        "parts": _score_parts(root),
        "warnings": warnings,
    }
    return MusicXmlScore(notes=notes, tempo_map=tempo_map.as_lightscore_tempo(), meta=meta)


def musicxml_to_lightscore(
    path: str,
    *,
    part: Optional[str] = None,
    hand_mode: str = "auto",
    meta: Optional[dict] = None,
) -> dict:
    """Ingest ``path`` -> :func:`parse_musicxml`, finger with
    :func:`~fingerlight_score.fingering.assign_fingering`, emit with
    :func:`~fingerlight_score.emit.to_lightscore`. Convenience glue only; no new
    logic lives here.

    ``hand_mode`` defaults to ``"auto"`` (see ``hands.py``): a genuine multi-staff
    source still gets exactly the old ``"staff"`` behaviour, byte-identical; a source
    with no ``<staff>`` elements at all (see the warning above) gets pitch-based hand
    splitting instead of being silently fingered as one hand. Pass ``hand_mode=
    "staff"`` explicitly to force the old behaviour unconditionally."""
    score = parse_musicxml(path, part=part)
    fingered = assign_fingering(score.notes, hand_mode=hand_mode)
    out_meta = {"source": "musicxml", "source_file": path}
    if score.meta.get("title"):
        out_meta["title"] = score.meta["title"]
    if meta:
        out_meta.update(meta)
    doc = to_lightscore(fingered, meta=out_meta)
    if score.tempo_map:
        doc["tempo"] = score.tempo_map
    return doc
