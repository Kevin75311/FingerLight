"""Tests for the OMR arm (project item A4), ``fingerlight_score.omr``.

Two halves, tested very differently (see ``omr.py``'s module docstring):

  * The Audiveris subprocess wrapper -- UNVERIFIED against a real binary (none is
    installed on this machine). Exercised only via mocked ``subprocess.run`` and via
    the real, unmocked "not found" path (Audiveris genuinely isn't installed here).
  * Post-OMR cleanup -- fully real, checked against
    ``fixtures/raw-audiveris-grand-staff.musicxml``, a hand-authored file that
    imitates raw Audiveris output (see ``fixtures/README.md``: never produced by
    Audiveris itself).
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import defusedxml.ElementTree as ET  # noqa: E402
import pytest  # noqa: E402

from fingerlight_score.musicxml import parse_musicxml  # noqa: E402
from fingerlight_score.omr import (  # noqa: E402
    AUDIVERIS_CLI_ARGS,
    AudiverisNotFoundError,
    OmrResult,
    _build_command,
    clean_omr_musicxml,
    locate_audiveris,
    merge_two_staff_parts,
    omr_to_lightscore,
    omr_to_musicxml,
    run_audiveris,
    sanity_report,
)

_HERE = os.path.dirname(__file__)
_FIXTURES = os.path.join(_HERE, "fixtures")
RAW_FIXTURE = os.path.join(_FIXTURES, "raw-audiveris-grand-staff.musicxml")


def _fresh_root():
    with open(RAW_FIXTURE, "rb") as fh:
        return ET.fromstring(fh.read())


# ---------------------------------------------------------------------------
# Subprocess wrapper -- binary lookup is real, the subprocess call itself is mocked
# ---------------------------------------------------------------------------


def test_locate_audiveris_raises_actionable_error_when_not_installed(monkeypatch):
    """Real (unmocked) check: java/Audiveris are genuinely absent on this machine,
    so this exercises the actual not-found path, not a simulation of it."""
    monkeypatch.delenv("FINGERLIGHT_AUDIVERIS", raising=False)
    with pytest.raises(AudiverisNotFoundError) as excinfo:
        locate_audiveris()
    msg = str(excinfo.value)
    assert "audiveris" in msg.lower()
    assert "github.com/Audiveris/audiveris/releases" in msg


def test_locate_audiveris_explicit_path_must_exist():
    with pytest.raises(AudiverisNotFoundError):
        locate_audiveris(explicit=r"C:\definitely\not\a\real\path\Audiveris.exe")


def test_build_command_matches_module_constant():
    cmd = _build_command("Audiveris.exe", "in.pdf", r"C:\out")
    assert cmd[0] == "Audiveris.exe"
    assert cmd[1:] == [
        tok.format(output_dir=r"C:\out", input_file="in.pdf") for tok in AUDIVERIS_CLI_ARGS
    ]
    # The flags this module claims to use, present verbatim (spelling matters).
    assert "-batch" in cmd
    assert "-export" in cmd
    assert "-output" in cmd


def test_run_audiveris_mocked_success(tmp_path):
    fake_binary = tmp_path / "Audiveris.exe"
    fake_binary.write_bytes(b"fake-binary")
    input_pdf = tmp_path / "song.pdf"
    input_pdf.write_bytes(b"%PDF-fake")
    out_dir = tmp_path / "out"

    def fake_run(cmd, capture_output, text, timeout):
        os.makedirs(out_dir, exist_ok=True)
        (out_dir / "song.mxl").write_bytes(b"fake-mxl")

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        result_path = run_audiveris(str(input_pdf), str(out_dir), binary=str(fake_binary))

    assert result_path == str(out_dir / "song.mxl")
    called_cmd = mock_run.call_args.args[0]
    assert called_cmd[0] == str(fake_binary)
    assert "-batch" in called_cmd


def test_run_audiveris_raises_with_nonzero_exit(tmp_path):
    fake_binary = tmp_path / "Audiveris.exe"
    fake_binary.write_bytes(b"fake-binary")
    input_pdf = tmp_path / "song.pdf"
    input_pdf.write_bytes(b"%PDF-fake")

    def fake_run(cmd, capture_output, text, timeout):
        class _Proc:
            returncode = 1
            stdout = "some log"
            stderr = "boom"

        return _Proc()

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(Exception) as excinfo:
            run_audiveris(str(input_pdf), str(tmp_path / "out"), binary=str(fake_binary))
    assert "boom" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Cleanup -- fully real, checked against the hand-authored fixture
# ---------------------------------------------------------------------------


def test_merge_two_staff_parts_collapses_to_one_part_with_staff_numbers():
    root = _fresh_root()
    warnings = merge_two_staff_parts(root)

    part_list = root.find("part-list")
    assert len(part_list.findall("score-part")) == 1
    assert len(root.findall("part")) == 1

    part = root.find("part")
    staffs = [n.find("staff").text for n in part.findall(".//note")]
    assert staffs.count("1") == 6  # P1's six treble notes
    assert staffs.count("2") == 4  # P2's four bass notes

    assert any(w.kind == "staff_merge" for w in warnings)


def test_sanity_report_flags_the_out_of_range_pitch():
    root = _fresh_root()
    merge_two_staff_parts(root)
    warnings = sanity_report(root)

    range_warnings = [w for w in warnings if w.kind == "out_of_range_pitch"]
    assert len(range_warnings) == 1
    assert "MIDI 12" in range_warnings[0].message


def test_clean_omr_musicxml_end_to_end(tmp_path):
    out_path = str(tmp_path / "cleaned.musicxml")
    result = clean_omr_musicxml(RAW_FIXTURE, out_path)

    assert isinstance(result, OmrResult)
    assert result.musicxml_path == out_path
    assert os.path.isfile(out_path)
    assert any(w.kind == "out_of_range_pitch" for w in result.warnings)
    assert any(w.kind == "staff_merge" for w in result.warnings)

    score = parse_musicxml(out_path)
    # 10 notes authored, 1 (C0, MIDI 12) is below the 88-key range and dropped by
    # parse_musicxml itself.
    assert len(score.notes) == 9, f"expected 9 notes, got {len(score.notes)}"

    staff2_notes = [n for n in score.notes if n.staff == 2]
    assert len(staff2_notes) == 3, f"expected 3 bass notes on staff 2, got {len(staff2_notes)}"
    assert sorted(n.pitch for n in staff2_notes) == [36, 43, 48]  # C2, G2, C3


def test_without_cleanup_the_fixture_parses_wrong():
    """Negative control (acceptance item 3): parsing the RAW fixture directly --
    skipping this module's cleanup entirely -- must produce a visibly wrong result,
    proving the cleanup is load-bearing rather than cosmetic."""
    score = parse_musicxml(RAW_FIXTURE)

    # _select_part picks only the first part with notes (P1) and warns about the
    # other; P1 carries no <staff>, so everything lands on staff 1 and the whole
    # bass part is missing.
    assert len(score.notes) == 6, (
        f"expected only P1's 6 treble notes with no cleanup, got {len(score.notes)}"
    )
    staff2_notes = [n for n in score.notes if n.staff == 2]
    assert len(staff2_notes) == 0, "uncleaned fixture must show 0 notes on staff 2 (the bug)"
    assert any("multiple parts contain notes" in w for w in score.meta["warnings"])


def test_omr_to_musicxml_dispatches_to_cleanup_for_musicxml_extension(tmp_path):
    """.musicxml input skips run_audiveris entirely (asserted by NOT mocking
    subprocess -- if it tried to invoke Audiveris this would raise AudiverisNotFoundError,
    which it must not)."""
    out_path = str(tmp_path / "cleaned.musicxml")
    result = omr_to_musicxml(RAW_FIXTURE, out_path)
    assert os.path.isfile(result.musicxml_path)
    score = parse_musicxml(result.musicxml_path)
    assert len(score.notes) == 9


def test_omr_to_lightscore_end_to_end(tmp_path):
    doc = omr_to_lightscore(RAW_FIXTURE, workdir=str(tmp_path))
    assert doc["meta"]["source"] == "musicxml"
    assert any("out_of_range_pitch" in w for w in doc["meta"]["omr_warnings"])
    hands = {n["stf"] for n in doc["notes"]}
    assert hands == {1, 2}
    left_hand_notes = [n for n in doc["notes"] if n["stf"] == 2]
    assert len(left_hand_notes) == 3
