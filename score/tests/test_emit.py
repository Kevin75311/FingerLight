import json
from pathlib import Path

import jsonschema
import pytest

from fingerlight_score.emit import to_lightscore, write_lightscore
from fingerlight_score.fingering import assign_fingering
from fingerlight_score.notes import Note

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "lightscore" / "schema" / "lightscore-v1.schema.json"
)
EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "lightscore"
    / "examples"
    / "c-major-scale-rh.lightscore.json"
)


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _c_major_fingered():
    pitches = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = [Note(t_ms=i * 500, d_ms=500, pitch=p, staff=1) for i, p in enumerate(pitches)]
    return assign_fingering(notes, hand_mode="staff")


def test_to_lightscore_validates_against_schema(schema):
    doc = to_lightscore(_c_major_fingered(), meta={"title": "test"})
    jsonschema.validate(instance=doc, schema=schema)


def test_to_lightscore_matches_fixture_fingering():
    # Ground-truth check: our emitted document's finger sequence should equal the
    # hand-written fixture's, since the DP is supposed to discover the same
    # textbook fingering.
    fixture = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    doc = to_lightscore(_c_major_fingered())
    assert [n["f"] for n in doc["notes"]] == [n["f"] for n in fixture["notes"]]
    assert [n["p"] for n in doc["notes"]] == [n["p"] for n in fixture["notes"]]
    assert [n["h"] for n in doc["notes"]] == [n["h"] for n in fixture["notes"]]


def test_to_lightscore_round_trips_through_json(schema):
    doc = to_lightscore(_c_major_fingered(), meta={"title": "round trip"})
    reloaded = json.loads(json.dumps(doc))
    assert reloaded == doc
    jsonschema.validate(instance=reloaded, schema=schema)


def test_to_lightscore_omits_meta_when_none():
    doc = to_lightscore(_c_major_fingered(), meta=None)
    assert "meta" not in doc


def test_to_lightscore_defaults_fingering_to_dp():
    doc = to_lightscore(_c_major_fingered(), meta={"title": "x"})
    assert doc["meta"]["fingering"] == "dp"


def test_to_lightscore_omits_velocity_when_absent():
    doc = to_lightscore(_c_major_fingered())
    assert all("v" not in n for n in doc["notes"])


def test_write_lightscore_writes_valid_json(tmp_path, schema):
    target = tmp_path / "out.lightscore.json"
    doc = write_lightscore(target, _c_major_fingered(), meta={"title": "write test"})

    assert target.exists()
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk == doc
    jsonschema.validate(instance=on_disk, schema=schema)
