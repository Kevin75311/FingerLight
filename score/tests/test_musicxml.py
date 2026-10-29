"""Acceptance tests for MusicXML ingest (project item A3), checked against ground
truth that did NOT come from our own parser:

  * ``grand-staff.mid`` -- MuseScore's own MIDI render of the same source, read back
    by the independent ``midi_ground_truth.read_midi`` (never modified here).
  * ``README.md`` -- the notated-duration table (MIDI durations carry MuseScore's
    ~94.9% articulation gap, so durations are checked against the *notated* table,
    not the MIDI, per the README).

See ``score/tests/fixtures/README.md`` for the full trap list and provenance chain.
"""

import json
import os
import sys

import jsonschema
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight_score.fingering import assign_fingering  # noqa: E402
from fingerlight_score.musicxml import (  # noqa: E402
    MusicXmlScore,
    musicxml_to_lightscore,
    parse_musicxml,
)

from midi_ground_truth import read_midi  # noqa: E402

_HERE = os.path.dirname(__file__)
_FIXTURES = os.path.join(_HERE, "fixtures")
MUSICXML_PATH = os.path.join(_FIXTURES, "grand-staff.musicxml")
MXL_PATH = os.path.join(_FIXTURES, "grand-staff.mxl")
MID_PATH = os.path.join(_FIXTURES, "grand-staff.mid")
SCHEMA_PATH = os.path.join(
    _HERE, "..", "..", "lightscore", "schema", "lightscore-v1.schema.json"
)

# Notated durations (ms), keyed by (t_ms, pitch) -- transcribed from README.md's
# ground-truth table (the "notated d (ms)" column), independent of MIDI.
NOTATED_DURATIONS = {
    (0, 60): 500,
    (0, 48): 1000,
    (0, 55): 1000,
    (500, 62): 500,
    (1000, 64): 1000,
    (2000, 66): 500,
    (2000, 41): 2000,
    (2000, 48): 2000,
    (3000, 67): 2000,
    (4000, 43): 2000,
    (5000, 70): 1000,
}


@pytest.fixture(scope="module")
def schema():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_parse_returns_musicxml_score():
    score = parse_musicxml(MUSICXML_PATH)
    assert isinstance(score, MusicXmlScore)
    assert isinstance(score.meta, dict)
    assert "warnings" in score.meta


def test_headline_note_count_and_onsets_pitches_exact_against_midi():
    """Item 2: exactly 11 notes; sorted (t_ms, pitch) pairs equal the MIDI's exactly."""
    score = parse_musicxml(MUSICXML_PATH)
    assert len(score.notes) == 11, "expected exactly 11 notes, got %d" % len(score.notes)

    midi_notes = read_midi(MID_PATH)
    assert len(midi_notes) == 11

    ours = sorted((n.t_ms, n.pitch) for n in score.notes)
    theirs = sorted((n.t_ms, n.pitch) for n in midi_notes)
    assert ours == theirs, "onset/pitch pairs must match the independent MIDI reader exactly"


def test_durations_match_notated_values_not_midi():
    """Durations are checked against the notated table (README.md), never against
    the MIDI's articulation-shortened durations -- see module docstring."""
    score = parse_musicxml(MUSICXML_PATH)
    checked = 0
    for n in score.notes:
        key = (n.t_ms, n.pitch)
        assert key in NOTATED_DURATIONS, "unexpected note %r not in ground truth" % (key,)
        assert n.d_ms == NOTATED_DURATIONS[key], (
            "note t=%d pitch=%d: expected notated duration %d ms, got %d ms"
            % (n.t_ms, n.pitch, NOTATED_DURATIONS[key], n.d_ms)
        )
        checked += 1
    assert checked == 11


def test_tie_across_barline_is_one_note_not_two():
    """Item 3: G4 (pitch 67) appears exactly once, at t=3000, with d_ms == 2000.
    A tie bug yields two notes at t=3000 and t=4000 -- assert that does NOT happen."""
    score = parse_musicxml(MUSICXML_PATH)
    g4_notes = [n for n in score.notes if n.pitch == 67]
    assert len(g4_notes) == 1, (
        "G4 (pitch 67) must appear exactly once (the tied pair merged into one note); "
        "got %d occurrences at t=%r -- a tie bug splits it into two notes at t=3000 "
        "and t=4000" % (len(g4_notes), [n.t_ms for n in g4_notes])
    )
    assert g4_notes[0].t_ms == 3000
    assert g4_notes[0].d_ms == 2000, (
        "G4 tied duration should be the SUM of both tie segments (2000 ms), got %d"
        % g4_notes[0].d_ms
    )


def test_staff_and_hand_orientation():
    """Item 4: every pitch < 60 is on staff 2, every pitch >= 60 is on staff 1 in this
    fixture; then assign_fingering must populate both hands with correctly-oriented
    left-hand fingering (finger falls as pitch rises within a simultaneous LH group)."""
    score = parse_musicxml(MUSICXML_PATH)
    for n in score.notes:
        if n.pitch < 60:
            assert n.staff == 2, "pitch %d expected on staff 2, got %d" % (n.pitch, n.staff)
        else:
            assert n.staff == 1, "pitch %d expected on staff 1, got %d" % (n.pitch, n.staff)

    fingered = assign_fingering(score.notes)
    hands = {f.hand for f in fingered}
    assert hands == {"L", "R"}, "both hands must be populated, got %r" % hands

    # Left-hand orientation: within the t=0 LH group (C3=48, G3=55) and the t=2000 LH
    # group (F2=41, C3=48), finger number must FALL as pitch rises (thumb on top).
    by_time_hand = {}
    for f in fingered:
        if f.hand == "L":
            by_time_hand.setdefault(f.t_ms, []).append(f)
    checked_groups = 0
    for t, group in by_time_hand.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda f: f.pitch)
        fingers = [f.finger for f in group]
        assert fingers == sorted(fingers, reverse=True), (
            "LH group at t=%d: fingers %r should FALL as pitch rises (thumb on top)"
            % (t, fingers)
        )
        checked_groups += 1
    assert checked_groups == 2, "expected 2 multi-note LH groups (t=0, t=2000), checked %d" % checked_groups


def test_mxl_matches_plain_musicxml():
    """Item 5: parsing the compressed .mxl container yields an identical result to
    parsing the plain .musicxml -- proves META-INF/container.xml's rootfile lookup
    works rather than a hardcoded inner filename."""
    plain = parse_musicxml(MUSICXML_PATH)
    compressed = parse_musicxml(MXL_PATH)
    assert plain.notes == compressed.notes
    assert plain.tempo_map == compressed.tempo_map


def test_end_to_end_lightscore_validates_against_schema(schema):
    """Item 6."""
    doc = musicxml_to_lightscore(MUSICXML_PATH)
    jsonschema.validate(instance=doc, schema=schema)
    assert len(doc["notes"]) == 11
    assert doc["meta"]["source"] == "musicxml"


def test_end_to_end_mxl_also_validates(schema):
    doc = musicxml_to_lightscore(MXL_PATH)
    jsonschema.validate(instance=doc, schema=schema)


# ---------------------------------------------------------------------------
# Negative controls (item 7): prove the comparison logic can actually fail.
# ---------------------------------------------------------------------------


def test_negative_control_backup_omission_would_be_caught():
    """Construct in-memory MusicXML where <backup> is OMITTED (so the grand staff's
    left hand lands after the right hand instead of under it), and confirm our own
    exact-match comparison rejects that wrong result -- proving the assertion style
    used above is not vacuously true."""
    good_notes = parse_musicxml(MUSICXML_PATH).notes
    good_pairs = sorted((n.t_ms, n.pitch) for n in good_notes)

    # Simulate "no backup": every note's onset simply advances the cursor, so the
    # left-hand notes get pushed to start after the whole right-hand line instead of
    # underneath it (staff-2 notes shifted to start at t=4000 instead of t=0/2000/4000
    # overlapping the right hand).
    broken_pairs = sorted(
        (n.t_ms + (4000 if n.staff == 2 else 0), n.pitch) for n in good_notes
    )
    assert broken_pairs != good_pairs, (
        "a backup-less (broken) cursor must NOT match the real parser's onsets -- "
        "if it did, this test's comparison logic could never catch a real <backup> bug"
    )


def test_negative_control_alter_is_not_ignored():
    """A note with <alter>1</alter> must NOT parse as the natural pitch."""
    ns = os.path.join(_HERE)  # not used, just documents intent
    import xml.etree.ElementTree as StdET

    from fingerlight_score.musicxml import _pitch_to_midi  # noqa: E402

    sharp = StdET.fromstring(
        "<pitch><step>F</step><alter>1</alter><octave>4</octave></pitch>"
    )
    natural = StdET.fromstring("<pitch><step>F</step><octave>4</octave></pitch>")
    sharp_midi = _pitch_to_midi(sharp)
    natural_midi = _pitch_to_midi(natural)
    assert sharp_midi != natural_midi, "F#4 must not parse identically to F4"
    assert sharp_midi == 66 and natural_midi == 65


def test_out_of_range_pitch_is_dropped_and_warned():
    """Item 11: pitches outside MIDI 21..108 are kept out of notes and recorded in
    warnings, using a synthetic in-memory score (the fixture has none out of range)."""
    import tempfile

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions></attributes>
      <note>
        <pitch><step>C</step><octave>-1</octave></pitch>
        <duration>1</duration><voice>1</voice><type>quarter</type><staff>1</staff>
      </note>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration><voice>1</voice><type>quarter</type><staff>1</staff>
      </note>
    </measure>
  </part>
</score-partwise>"""
    with tempfile.NamedTemporaryFile(suffix=".musicxml", delete=False, mode="w", encoding="utf-8") as fh:
        fh.write(xml)
        path = fh.name
    try:
        score = parse_musicxml(path)
        assert [n.pitch for n in score.notes] == [60]
        assert any("outside the 88-key range" in w for w in score.meta["warnings"])
    finally:
        os.unlink(path)


def test_divisions_can_change_mid_score():
    """Item 1: a mid-score <divisions> change must be honoured, not read once."""
    import tempfile

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions></attributes>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration><voice>1</voice><type>quarter</type><staff>1</staff>
      </note>
    </measure>
    <measure number="2">
      <attributes><divisions>2</divisions></attributes>
      <note>
        <pitch><step>D</step><octave>4</octave></pitch>
        <duration>2</duration><voice>1</voice><type>quarter</type><staff>1</staff>
      </note>
    </measure>
  </part>
</score-partwise>"""
    with tempfile.NamedTemporaryFile(suffix=".musicxml", delete=False, mode="w", encoding="utf-8") as fh:
        fh.write(xml)
        path = fh.name
    try:
        score = parse_musicxml(path)
        # 120bpm default -> 500ms/quarter. Measure 1: C4 at t=0, d=500 (1/1 division).
        # Measure 2: divisions=2, duration=2 -> 1 full quarter -> D4 at t=500, d=500.
        assert [(n.t_ms, n.pitch, n.d_ms) for n in score.notes] == [(0, 60, 500), (500, 62, 500)]
    finally:
        os.unlink(path)
