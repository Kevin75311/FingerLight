"""Independent acceptance for the MusicXML ingest, on the features the first fixture
cannot reach: grace notes, a mid-score tempo change, repeat barlines, and a second voice
sharing one staff.

Written separately from `test_musicxml.py` and against a separately generated fixture,
because the ingest's own report listed these four as "implemented but verified by code
reading only".  A claim verified by reading is not verified.

Ground truth is MuseScore's MIDI render of the same source (see fixtures/README.md).
The repeat is the interesting case: **MuseScore unfolds it and we deliberately do not**,
so the two disagree by design -- and that disagreement is itself asserted below, rather
than being allowed to look like a bug later.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from fingerlight_score.fingering import assign_fingering  # noqa: E402
from fingerlight_score.musicxml import parse_musicxml  # noqa: E402
from midi_ground_truth import read_midi  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
HARD_XML = os.path.join(_HERE, "fixtures", "hard.musicxml")
HARD_MID = os.path.join(_HERE, "fixtures", "hard.mid")

#: Where the repeated pass begins in MuseScore's render.  Measure 2 runs 2000..6000 ms
#: at 60 bpm, so the second pass starts here.
REPEAT_PASS_START_MS = 6000


@pytest.fixture(scope="module")
def parsed():
    return parse_musicxml(HARD_XML)


@pytest.fixture(scope="module")
def midi():
    return read_midi(HARD_MID)


def test_fixture_exists():
    """Regenerate with MuseScore if this fails -- see fixtures/README.md."""
    assert os.path.exists(HARD_XML) and os.path.exists(HARD_MID)


# ------------------------------------------------- the single-pass cross-check


def test_first_pass_matches_midi_exactly(parsed, midi):
    """Our notes must equal MuseScore's, restricted to the first pass and excluding
    the zero-length grace note."""
    theirs = sorted(
        (n.t_ms, n.pitch)
        for n in midi
        if n.t_ms < REPEAT_PASS_START_MS and n.d_ms > 0
    )
    ours = sorted((n.t_ms, n.pitch) for n in parsed.notes)
    assert ours == theirs, "ours=%r theirs=%r" % (ours, theirs)
    assert len(ours) == 8, "expected 8 notes in one pass, got %d" % len(ours)


def test_repeat_is_not_unfolded_and_is_warned(parsed, midi):
    """MuseScore plays measure 2 twice; we play it once and say so.

    If this ever starts passing silently without the warning, a score with repeats
    would quietly lose half its music.
    """
    assert any(n.t_ms >= REPEAT_PASS_START_MS for n in midi), (
        "ground truth should contain a second pass; fixture may have been regenerated "
        "without the repeat barlines"
    )
    assert max(n.t_ms for n in parsed.notes) < REPEAT_PASS_START_MS, (
        "parser appears to have unfolded the repeat"
    )
    assert any("repeat" in w.lower() for w in parsed.meta["warnings"]), (
        "repeats were dropped without a warning: %r" % (parsed.meta["warnings"],)
    )


# --------------------------------------------------------- mid-score tempo change


def test_tempo_change_is_honoured(parsed):
    """Measure 1 is 120 bpm (quarter = 500 ms), measure 2 is 60 bpm (quarter = 1000 ms).

    A parser that reads tempo once puts every measure-2 note at the wrong time -- and
    nothing about the output would look malformed.
    """
    by_t = {(n.t_ms, n.pitch): n for n in parsed.notes}
    # Measure 1, at 120 bpm: quarter notes are 500 ms and land on 0 / 500.
    assert by_t[(0, 60)].d_ms == 500
    assert by_t[(500, 64)].d_ms == 500
    assert by_t[(1000, 67)].d_ms == 1000            # half note
    # Measure 2 begins at 2000 ms and runs at 60 bpm: a half note is now 2000 ms.
    assert by_t[(2000, 60)].d_ms == 2000, "measure-2 half note should be 2000 ms at 60bpm"
    assert by_t[(4000, 62)].d_ms == 2000
    assert by_t[(2000, 43)].d_ms == 4000, "measure-2 whole note should be 4000 ms"


def test_measure_two_is_twice_as_long_as_measure_one(parsed):
    """The tempo halved, so the same notated bar takes twice the time."""
    m1 = 2000       # 4 quarters at 500 ms
    m2_start = min(n.t_ms for n in parsed.notes if n.t_ms >= m1)
    assert m2_start == m1
    m2_end = max(n.t_ms + n.d_ms for n in parsed.notes)
    assert m2_end - m2_start == 4000, "measure 2 should span 4000 ms at 60 bpm"


# ------------------------------------------------------------------ grace notes


def test_grace_note_is_skipped_and_warned(parsed):
    """A grace note has no <duration>. Skipping is a legitimate choice; skipping
    *silently* is not -- it would shift nothing but would drop a cue the player sees
    in the score."""
    assert not any(n.pitch == 62 and n.t_ms == 0 for n in parsed.notes), (
        "the grace D4 should not appear as a normal note at t=0"
    )
    assert any("grace" in w.lower() for w in parsed.meta["warnings"]), (
        "grace note dropped without a warning: %r" % (parsed.meta["warnings"],)
    )


def test_grace_note_does_not_shift_the_notes_after_it(parsed):
    """The real danger of a grace note: consuming cursor time it does not have.
    If it did, every following note in the measure would slide."""
    assert any(n.t_ms == 0 and n.pitch == 60 for n in parsed.notes), (
        "C4 should still be at t=0; the grace note stole cursor time"
    )
    assert any(n.t_ms == 500 and n.pitch == 64 for n in parsed.notes)


# ------------------------------------------------- two voices sharing one staff


def test_second_voice_stays_on_its_own_staff(parsed):
    """A3 is voice 2 but staff 1. A parser keying the hand off *voice* instead of
    *staff* sends it to the left hand, and the fingering comes out nonsense."""
    a3 = [n for n in parsed.notes if n.pitch == 57]
    assert len(a3) == 1, "expected exactly one A3, got %d" % len(a3)
    assert a3[0].staff == 1, (
        "A3 is voice 2 on staff 1; got staff %d -- voice was mistaken for staff"
        % a3[0].staff
    )
    assert a3[0].t_ms == 0 and a3[0].d_ms == 2000


def test_multiple_voices_are_warned(parsed):
    assert any("voice" in w.lower() for w in parsed.meta["warnings"])


def test_staff_split_still_produces_two_usable_hands(parsed):
    """The whole point of getting staff right: hand assignment downstream."""
    fingered = assign_fingering(parsed.notes)
    assert {f.hand for f in fingered} == {"L", "R"}
    # staff 2 -> left hand
    lows = {f.pitch for f in fingered if f.hand == "L"}
    assert lows == {48, 43}, "left hand should be exactly the staff-2 notes, got %r" % lows
    assert all(1 <= f.finger <= 5 for f in fingered)


# ------------------------------------ a score with no <staff> at all (Q-09 territory)

_SINGLE_STAFF = """<?xml version="1.0"?><score-partwise version="4.0">
<part-list><score-part id="P1"><part-name>P</part-name></score-part></part-list>
<part id="P1"><measure number="1">
<attributes><divisions>1</divisions><clef><sign>G</sign><line>2</line></clef></attributes>
<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice></note>
<note><pitch><step>C</step><octave>3</octave></pitch><duration>1</duration><voice>1</voice></note>
</measure></part></score-partwise>"""


def test_score_with_no_staff_elements_is_warned(tmp_path):
    """The quietest failure mode in the whole ingest.

    Hand assignment keys off `<staff>`. A score carrying none -- a lead sheet, or a
    bare export -- lands entirely on staff 1 and gets fingered as though one hand
    played everything, including the bass. The output is structurally perfect: right
    note count, legal fingers, valid light-score. Only the hands are wrong, and on the
    bar that means every left-hand cue lights in a right-hand colour.

    This is the concrete case the project's Q-09 exists to solve. Until it is, the
    ingest must at least say so.
    """
    path = tmp_path / "single.musicxml"
    path.write_text(_SINGLE_STAFF, encoding="utf-8")
    sc = parse_musicxml(str(path))

    assert {n.staff for n in sc.notes} == {1}
    assert any("no <staff>" in w for w in sc.meta["warnings"]), (
        "a staff-less score was accepted silently: %r" % (sc.meta["warnings"],)
    )


def test_the_no_staff_warning_does_not_false_positive(parsed):
    """It must not fire on a real grand staff, or it becomes noise people ignore."""
    assert not any("no <staff>" in w for w in parsed.meta["warnings"]), (
        "warning fired on a genuine two-staff score: %r" % (parsed.meta["warnings"],)
    )


# ------------------------------------------------------------- negative control


def test_the_cross_check_can_fail(parsed, midi):
    """Sabotage: including MuseScore's second pass must break the comparison, proving
    the first-pass filter is doing real work rather than matching trivially."""
    unfiltered = sorted((n.t_ms, n.pitch) for n in midi if n.d_ms > 0)
    ours = sorted((n.t_ms, n.pitch) for n in parsed.notes)
    assert ours != unfiltered, (
        "parsed notes equal the UNFOLDED midi -- the repeat filter proves nothing"
    )
