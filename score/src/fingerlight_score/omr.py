"""OMR ingest (Part 3, project item A4): turn a PDF or photo of sheet music into a
MusicXML file that ``musicxml.py`` can already read.

Design (settled, not relitigated here): OMR runs **offline on the PC**, never on the
Pi Zero 2 W -- the OMR engine (Audiveris, a JVM app wanting 2GB+ heap) is invoked as a
subprocess from this module, producing a compressed ``.mxl`` that already-existing
:mod:`fingerlight_score.musicxml` reads.

Two halves, verified very differently:

1. **The subprocess wrapper** (:func:`locate_audiveris`, :data:`AUDIVERIS_CLI_ARGS`,
   :func:`run_audiveris`) -- the exact command line is verified against Audiveris'
   own published handbook (fetched 2026-09-16):

     * CLI flags -- ``-batch``, ``-transcribe``, ``-export``, ``-output DIRNAME``:
       https://audiveris.github.io/audiveris/_pages/guides/advanced/cli/
     * Windows install path (``C:\\Program Files\\Audiveris\\Audiveris.exe``):
       https://audiveris.github.io/audiveris/_pages/tutorials/install/binaries/

   **This half is UNVERIFIED on this machine.** There is no JVM and no Audiveris
   install here (see ``docs/score/omr.md``), so ``run_audiveris`` has never actually
   been exec'd against the real binary -- only its command-line construction and
   binary-lookup logic are exercised, by mocking ``subprocess.run``. If Audiveris'
   real CLI rejects :data:`AUDIVERIS_CLI_ARGS`, fix it there -- it is a plain module
   constant, deliberately kept easy to inspect and correct.

2. **Post-OMR cleanup** (:func:`merge_two_staff_parts`, :func:`sanity_report`,
   :func:`clean_omr_musicxml`) -- fully testable today, and the part that actually
   matters: raw OMR output is not clean MusicXML, and ``score/tests/fixtures/`` has a
   hand-authored file that imitates its most damaging defect (see
   ``score/tests/fixtures/README.md``: this fixture was written to imitate Audiveris,
   never produced by it).

Public surface::

    from fingerlight_score.omr import omr_to_musicxml, omr_to_lightscore

``omr_to_musicxml`` accepts either an image/PDF (invokes Audiveris) or an
already-produced raw MusicXML/``.mxl`` file (skips straight to cleanup -- this is how
the test fixture, and any re-run of cleanup alone, is exercised without a real OMR
pass).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from fractions import Fraction
from typing import List, Optional, Tuple

try:
    import defusedxml.ElementTree as ET  # type: ignore[import]

    _USING_DEFUSEDXML = True
except ImportError:  # pragma: no cover - environment without defusedxml
    import xml.etree.ElementTree as ET  # type: ignore[import]

    _USING_DEFUSEDXML = False

# Tree *construction* (Element/SubElement/tostring) -- defusedxml.ElementTree only
# guards parsing of untrusted input; it does not provide element-building helpers.
import xml.etree.ElementTree as ETW

from .musicxml import MAX_MIDI_PITCH, MIN_MIDI_PITCH, musicxml_to_lightscore

# ---------------------------------------------------------------------------
# Locating and invoking Audiveris -- UNVERIFIED on this machine, see module docstring
# ---------------------------------------------------------------------------

AUDIVERIS_ENV_VAR = "FINGERLIGHT_AUDIVERIS"
AUDIVERIS_WINDOWS_DEFAULT = r"C:\Program Files\Audiveris\Audiveris.exe"
_AUDIVERIS_BINARY_NAMES = ("Audiveris.exe", "Audiveris", "audiveris.exe", "audiveris")

#: The exact Audiveris batch command line, as a module-level constant so it is easy
#: to inspect and correct later without hunting through the function body. Verified
#: against the handbook's CLI page (see module docstring), NOT against a real binary.
#: ``{output_dir}``/``{input_file}`` are filled in by :func:`_build_command`.
AUDIVERIS_CLI_ARGS: Tuple[str, ...] = (
    "-batch",
    "-transcribe",
    "-export",
    "-output",
    "{output_dir}",
    "--",
    "{input_file}",
)


class AudiverisNotFoundError(RuntimeError):
    """Raised when no Audiveris binary can be located by any of the lookup rules."""


class OmrSubprocessError(RuntimeError):
    """Raised when the Audiveris subprocess fails or produces no ``.mxl``."""


def locate_audiveris(explicit: Optional[str] = None) -> str:
    """Find the Audiveris binary, in order: ``explicit`` argument, then the
    ``FINGERLIGHT_AUDIVERIS`` env var, then ``PATH``, then the standard Windows
    install location. Raises :class:`AudiverisNotFoundError` naming exactly what to
    install/set if nothing is found -- never guesses silently."""
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        raise AudiverisNotFoundError(f"explicit Audiveris path {explicit!r} is not a file")

    env = os.environ.get(AUDIVERIS_ENV_VAR)
    if env:
        if os.path.isfile(env):
            return env
        raise AudiverisNotFoundError(
            f"{AUDIVERIS_ENV_VAR}={env!r} does not point to a file"
        )

    for name in _AUDIVERIS_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return found

    if os.path.isfile(AUDIVERIS_WINDOWS_DEFAULT):
        return AUDIVERIS_WINDOWS_DEFAULT

    raise AudiverisNotFoundError(
        "Audiveris not found. Install it from "
        "https://github.com/Audiveris/audiveris/releases (Windows installer), then "
        "either: add it to PATH; set " + AUDIVERIS_ENV_VAR + "=<path to Audiveris.exe>; "
        "or install to the default location " + AUDIVERIS_WINDOWS_DEFAULT + ". "
        "See docs/score/omr.md for the verified command line."
    )


def _build_command(binary: str, input_path: str, output_dir: str) -> List[str]:
    return [binary] + [
        tok.format(output_dir=output_dir, input_file=input_path) for tok in AUDIVERIS_CLI_ARGS
    ]


def run_audiveris(
    input_path: str,
    output_dir: str,
    *,
    binary: Optional[str] = None,
    timeout: Optional[float] = 600,
) -> str:
    """Run Audiveris in batch mode on ``input_path`` (PDF or image), collect the
    produced ``.mxl`` from ``output_dir``, and return its path.

    **UNVERIFIED on this machine** -- see module docstring. Audiveris names its
    output after the input's basename (``<stem>.mxl``); if the real binary names it
    differently, this falls back to "the one ``.mxl`` written to ``output_dir``" and
    raises :class:`OmrSubprocessError` with Audiveris' own stdout/stderr if that is
    still ambiguous, rather than guessing a wrong file.
    """
    resolved = locate_audiveris(binary)
    os.makedirs(output_dir, exist_ok=True)
    cmd = _build_command(resolved, input_path, output_dir)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise OmrSubprocessError(
            f"Audiveris exited {proc.returncode} for {input_path!r}\n"
            f"command: {cmd!r}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    stem = os.path.splitext(os.path.basename(input_path))[0]
    expected = os.path.join(output_dir, stem + ".mxl")
    if os.path.isfile(expected):
        return expected

    matches = [f for f in os.listdir(output_dir) if f.lower().endswith(".mxl")]
    if len(matches) == 1:
        return os.path.join(output_dir, matches[0])

    raise OmrSubprocessError(
        f"Audiveris ran (exit 0) but no single .mxl was found in {output_dir!r} "
        f"(expected {expected!r}, found {matches!r})\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Structured warnings -- never silently dropped
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OmrWarning:
    """One cleanup/sanity finding. ``kind`` is a stable machine-checkable tag;
    ``message`` is the human-readable detail."""

    kind: str
    message: str


@dataclass
class OmrResult:
    musicxml_path: str
    warnings: List[OmrWarning] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared helpers (small, deliberate duplicates of musicxml.py's private helpers --
# this module must not import or modify musicxml.py's internals; see its docstring)
# ---------------------------------------------------------------------------

_STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _pitch_to_midi(pitch_el) -> int:
    step = pitch_el.find("step").text.strip()
    octave = int(pitch_el.find("octave").text)
    alter_el = pitch_el.find("alter")
    alter = int(round(float(alter_el.text))) if alter_el is not None and alter_el.text else 0
    return (octave + 1) * 12 + _STEP_SEMITONE[step] + alter


@dataclass
class _MeasureState:
    """(divisions, beats, beat-type) in effect at the START of one measure."""

    divisions: int
    beats: int
    beat_type: int


def _track_measure_states(part_el) -> List[_MeasureState]:
    """Running (divisions, time-signature) state at the start of each measure of
    ``part_el``, carried forward across measures the way ``musicxml.py``'s own
    cursor model does (defaults: divisions=1, 4/4, per the MusicXML spec)."""
    divisions, beats, beat_type = 1, 4, 4
    out: List[_MeasureState] = []
    for measure in part_el.findall("measure"):
        out.append(_MeasureState(divisions, beats, beat_type))
        for child in measure:
            if child.tag == "attributes":
                d = child.find("divisions")
                if d is not None and d.text:
                    divisions = int(float(d.text))
                t = child.find("time")
                if t is not None:
                    b_el = t.find("beats")
                    bt_el = t.find("beat-type")
                    if b_el is not None and b_el.text:
                        beats = int(b_el.text)
                    if bt_el is not None and bt_el.text:
                        beat_type = int(bt_el.text)
    return out


def _measure_advance(measure_el) -> int:
    """Net cursor advance across one ``<measure>``, in whatever divisions are active
    at its start -- mirrors ``musicxml.py``'s ``_walk_part`` cursor model applied to a
    single measure in isolation: a non-chord note/rest advances, a chord note shares
    the previous onset (does not advance), ``<backup>``/``<forward>`` move the cursor
    explicitly, and grace notes (no ``<duration>``) are skipped. For a well-formed
    measure this equals the notated measure length."""
    cursor = 0
    pending = 0
    for child in measure_el:
        tag = child.tag
        if tag == "backup":
            cursor += pending
            pending = 0
            dur_el = child.find("duration")
            cursor -= int(dur_el.text) if dur_el is not None and dur_el.text else 0
        elif tag == "forward":
            cursor += pending
            pending = 0
            dur_el = child.find("duration")
            cursor += int(dur_el.text) if dur_el is not None and dur_el.text else 0
        elif tag == "note":
            if child.find("grace") is not None:
                continue
            dur_el = child.find("duration")
            dur = int(dur_el.text) if dur_el is not None and dur_el.text else 0
            if child.find("chord") is not None:
                pending = max(pending, dur)
            else:
                cursor += pending
                pending = dur
    cursor += pending
    return cursor


def _inject_staff(measure_el, staff_num: int) -> None:
    for note in measure_el.findall("note"):
        if note.find("staff") is None:
            ETW.SubElement(note, "staff").text = str(staff_num)


# ---------------------------------------------------------------------------
# Cleanup #1: grand staff exported as two separate <part>s with no <staff> markup
# ---------------------------------------------------------------------------


def merge_two_staff_parts(root) -> List[OmrWarning]:
    """Detect Audiveris' most damaging grand-staff defect -- each staff exported as
    its own ``<part>``, with no ``<staff>`` element anywhere -- and rewrite the tree
    **in place** into one ``<part>`` with ``<staff>1</staff>``/``<staff>2</staff>``
    injected into every note. Without this, ``musicxml.py`` picks only the first part
    that has notes (warning about the other) and the whole piece is fingered as one
    hand -- see ``musicxml.py:168`` (``_select_part``) and ``musicxml.py:361``
    (the "no ``<staff>`` elements" warning).

    Trigger (conservative by design -- anything else is left untouched and reported,
    never guessed): exactly two ``<score-part>`` entries in ``<part-list>``, both with
    a matching ``<part>``, and **no** ``<note>`` in either part already carries a
    ``<staff>`` element.

    Staff assignment: the part with the higher average MIDI pitch across its pitched
    notes becomes staff 1 (right hand); the other becomes staff 2. Audiveris does not
    guarantee ``<part-list>`` order matches staff order, so this is decided by pitch,
    not position.

    The two parts' measures are spliced with an explicit ``<backup>`` (its duration
    computed by :func:`_measure_advance` on the top part's own measure) and an
    ``<attributes><divisions>...</divisions></attributes>`` re-declaration before each
    spliced block, so a divisions mismatch between the two source parts is handled
    without rescaling any duration number -- exactly the mechanism ``musicxml.py``
    already reads for any multi-voice/multi-staff part (see its module docstring,
    "Cursor model").
    """
    warnings: List[OmrWarning] = []
    part_list = root.find("part-list")
    if part_list is None:
        return warnings
    score_parts = part_list.findall("score-part")
    if len(score_parts) != 2:
        return warnings

    parts_by_id = {p.get("id"): p for p in root.findall("part")}
    ids = [sp.get("id") for sp in score_parts]
    part_els = [parts_by_id.get(pid) for pid in ids]
    if any(p is None for p in part_els):
        return warnings

    any_staff = any(
        note.find("staff") is not None for part_el in part_els for note in part_el.findall(".//note")
    )
    if any_staff:
        return warnings

    def avg_pitch(part_el) -> Optional[float]:
        pitches = []
        for note in part_el.findall(".//note"):
            if note.find("rest") is not None or note.find("grace") is not None:
                continue
            pitch_el = note.find("pitch")
            if pitch_el is not None:
                pitches.append(_pitch_to_midi(pitch_el))
        return (sum(pitches) / len(pitches)) if pitches else None

    avgs = [avg_pitch(p) for p in part_els]
    if avgs[0] is None or avgs[1] is None:
        warnings.append(
            OmrWarning(
                "staff_merge_skipped",
                f"parts {ids!r} have no <staff> markup, but one has no pitched notes "
                "to rank by average pitch; left as separate parts",
            )
        )
        return warnings

    top_idx = 0 if avgs[0] >= avgs[1] else 1
    bot_idx = 1 - top_idx
    top_part, bot_part = part_els[top_idx], part_els[bot_idx]

    top_measures = top_part.findall("measure")
    bot_measures = bot_part.findall("measure")
    if len(top_measures) != len(bot_measures):
        warnings.append(
            OmrWarning(
                "staff_merge_skipped",
                f"part {ids[top_idx]!r} ({len(top_measures)} measures) and "
                f"{ids[bot_idx]!r} ({len(bot_measures)} measures) have different "
                "measure counts; left as separate parts",
            )
        )
        return warnings

    top_states = _track_measure_states(top_part)
    bot_states = _track_measure_states(bot_part)

    for i, (tm, bm) in enumerate(zip(top_measures, bot_measures)):
        _inject_staff(tm, 1)
        _inject_staff(bm, 2)
        advance = _measure_advance(tm)

        top_attrs = ETW.Element("attributes")
        ETW.SubElement(top_attrs, "divisions").text = str(top_states[i].divisions)
        if i == 0:
            ETW.SubElement(top_attrs, "staves").text = "2"
        tm.insert(0, top_attrs)

        backup_el = ETW.Element("backup")
        ETW.SubElement(backup_el, "duration").text = str(advance)
        tm.append(backup_el)

        bot_attrs = ETW.Element("attributes")
        ETW.SubElement(bot_attrs, "divisions").text = str(bot_states[i].divisions)
        tm.append(bot_attrs)

        for child in list(bm):
            tm.append(child)

    root.remove(bot_part)
    part_list.remove(score_parts[bot_idx])
    for pg in list(part_list.findall("part-group")):
        part_list.remove(pg)

    warnings.append(
        OmrWarning(
            "staff_merge",
            f"merged part {ids[top_idx]!r} (avg pitch {avgs[top_idx]:.1f} -> staff 1) "
            f"and part {ids[bot_idx]!r} (avg pitch {avgs[bot_idx]:.1f} -> staff 2) "
            "into one part",
        )
    )
    return warnings


# ---------------------------------------------------------------------------
# Cleanup #2: confidence / sanity report -- never silently dropped
# ---------------------------------------------------------------------------


def sanity_report(root) -> List[OmrWarning]:
    """Scan every ``<part>`` for the defects that would otherwise break or silently
    mislead ``parse_musicxml``: out-of-range pitches (outside MIDI 21..108, the
    88-key range), zero/negative/missing durations, measures whose total duration
    does not match the declared time signature, and notes with no pitch and no rest.
    Returns structured findings -- nothing here is fixed or dropped, only reported.

    A legitimate pickup (anacrusis) measure will report as a duration mismatch --
    that is a known false positive of this check, not a bug; it is a *sanity* report,
    not a hard validator.
    """
    warnings: List[OmrWarning] = []
    for part_el in root.findall("part"):
        pid = part_el.get("id", "?")
        states = _track_measure_states(part_el)
        any_staff = False

        for i, measure in enumerate(part_el.findall("measure")):
            mnum = measure.get("number", str(i + 1))
            state = states[i]

            for note in measure.findall("note"):
                if note.find("staff") is not None:
                    any_staff = True
                is_grace = note.find("grace") is not None
                is_rest = note.find("rest") is not None
                pitch_el = note.find("pitch")

                if not is_rest and pitch_el is None:
                    warnings.append(
                        OmrWarning(
                            "no_pitch",
                            f"part {pid} measure {mnum}: <note> has neither <pitch> "
                            "nor <rest> (e.g. <unpitched>); parse_musicxml will skip it",
                        )
                    )

                if pitch_el is not None:
                    midi = _pitch_to_midi(pitch_el)
                    if midi < MIN_MIDI_PITCH or midi > MAX_MIDI_PITCH:
                        warnings.append(
                            OmrWarning(
                                "out_of_range_pitch",
                                f"part {pid} measure {mnum}: pitch MIDI {midi} is "
                                f"outside the 88-key range {MIN_MIDI_PITCH}.."
                                f"{MAX_MIDI_PITCH}",
                            )
                        )

                if not is_grace:
                    dur_el = note.find("duration")
                    dur = int(dur_el.text) if dur_el is not None and dur_el.text else None
                    if dur is None or dur <= 0:
                        warnings.append(
                            OmrWarning(
                                "bad_duration",
                                f"part {pid} measure {mnum}: note has "
                                + ("no <duration>" if dur is None else f"duration {dur}")
                                + " (must be a positive integer)",
                            )
                        )

            expected = Fraction(state.beats * 4, state.beat_type) * state.divisions
            actual = _measure_advance(measure)
            if Fraction(actual) != expected:
                warnings.append(
                    OmrWarning(
                        "measure_duration_mismatch",
                        f"part {pid} measure {mnum}: total duration {actual} "
                        f"({state.divisions}/quarter units) does not match the "
                        f"{state.beats}/{state.beat_type} time signature "
                        f"(expected {expected})",
                    )
                )

        if not any_staff and part_el.find("measure/note") is not None:
            warnings.append(
                OmrWarning(
                    "single_part_no_staff",
                    f"part {pid}: no <staff> markup anywhere; parse_musicxml will "
                    "default everything to staff 1 (project open question Q-09, "
                    "split-by-pitch hand assignment -- not solved here)",
                )
            )

    return warnings


# ---------------------------------------------------------------------------
# XML I/O (small, deliberate duplicate of musicxml.py's private container reader --
# see the "shared helpers" note above)
# ---------------------------------------------------------------------------


def _read_xml_bytes(path: str) -> bytes:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            container = ET.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.find("./rootfiles/rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise ValueError(f"{path}: META-INF/container.xml has no rootfile full-path")
            return zf.read(rootfile.get("full-path"))
    with open(path, "rb") as fh:
        return fh.read()


def _write_musicxml(root, output_path: str) -> None:
    """Serialize ``root`` to ``output_path``, writing to a temp file and
    ``os.replace``-ing it into place (never truncate the destination before the
    bytes are ready -- see lessons.md, ``open(path, "w")`` truncates first)."""
    out_bytes = ETW.tostring(root, encoding="utf-8", xml_declaration=True)
    tmp_path = output_path + ".tmp"
    with open(tmp_path, "wb") as fh:
        fh.write(out_bytes)
    os.replace(tmp_path, output_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_RAW_MUSICXML_EXTENSIONS = (".musicxml", ".xml", ".mxl")


def clean_omr_musicxml(raw_path: str, output_path: Optional[str] = None) -> OmrResult:
    """Read raw OMR output (a ``.musicxml``/``.xml``/``.mxl`` file straight out of
    Audiveris, or anything imitating its shape -- see the test fixture), apply
    :func:`merge_two_staff_parts` and :func:`sanity_report`, and write a cleaned
    ``.musicxml`` file that :func:`fingerlight_score.musicxml.parse_musicxml` reads
    correctly. Returns an :class:`OmrResult` -- every finding is reported, never
    silently dropped or fixed invisibly."""
    xml_bytes = _read_xml_bytes(raw_path)
    root = ET.fromstring(xml_bytes)
    if root.tag != "score-partwise":
        raise ValueError(f"{raw_path}: only <score-partwise> is supported, got <{root.tag}>")

    warnings = list(merge_two_staff_parts(root))
    warnings.extend(sanity_report(root))

    if output_path is None:
        stem = os.path.splitext(os.path.basename(raw_path))[0]
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(raw_path)), stem + ".cleaned.musicxml"
        )

    _write_musicxml(root, output_path)
    return OmrResult(musicxml_path=output_path, warnings=warnings)


def omr_to_musicxml(
    input_path: str,
    output_path: Optional[str] = None,
    *,
    audiveris_binary: Optional[str] = None,
    workdir: Optional[str] = None,
) -> OmrResult:
    """PDF/image -> cleaned ``.musicxml`` path (plus structured warnings).

    If ``input_path`` already looks like MusicXML (``.musicxml``/``.xml``/``.mxl`` --
    i.e. it is already raw OMR output, or a stand-in for it such as the test
    fixture), Audiveris is skipped entirely and cleanup runs directly. Otherwise
    :func:`run_audiveris` is invoked first (**UNVERIFIED on this machine**, see
    module docstring) and its ``.mxl`` output is fed into the same cleanup path.

    If ``output_path`` is omitted, the cleaned file is written into ``workdir`` when
    one was given (so callers -- and tests -- passing ``workdir`` never spill output
    next to a read-only input such as a fixture); with neither given it falls back to
    :func:`clean_omr_musicxml`'s own default, next to ``raw_path``.
    """
    ext = os.path.splitext(input_path)[1].lower()
    if ext in _RAW_MUSICXML_EXTENSIONS:
        raw_path = input_path
        default_dir = workdir
    else:
        work = workdir or tempfile.mkdtemp(prefix="fingerlight_omr_")
        raw_path = run_audiveris(input_path, work, binary=audiveris_binary)
        default_dir = work

    if output_path is None and default_dir:
        stem = os.path.splitext(os.path.basename(raw_path))[0]
        output_path = os.path.join(default_dir, stem + ".cleaned.musicxml")

    return clean_omr_musicxml(raw_path, output_path)


def omr_to_lightscore(
    input_path: str,
    *,
    audiveris_binary: Optional[str] = None,
    workdir: Optional[str] = None,
    hand_mode: str = "staff",
    meta: Optional[dict] = None,
) -> dict:
    """Convenience: :func:`omr_to_musicxml` ->
    :func:`~fingerlight_score.musicxml.musicxml_to_lightscore`. No new logic lives
    here; OMR warnings are carried into the emitted light-score's ``meta`` so they
    are not lost."""
    result = omr_to_musicxml(input_path, audiveris_binary=audiveris_binary, workdir=workdir)
    out_meta = {"omr_warnings": [f"{w.kind}: {w.message}" for w in result.warnings]}
    if meta:
        out_meta.update(meta)
    return musicxml_to_lightscore(result.musicxml_path, hand_mode=hand_mode, meta=out_meta)
