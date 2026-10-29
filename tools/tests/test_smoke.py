"""Tests for the build-day smoke test: the coverage bookkeeping, and the key step driven
end to end by a fake piano (a stand-in `mido`) into a dry-run strip.  No Pi needed."""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hw"))

import smoke  # noqa: E402


def test_coverage_counts_first_strikes_releases_and_strays():
    c = smoke.KeyCoverage()
    assert c.note(60, True, 80) is True
    assert c.note(60, True, 90) is False            # a repeat is not a new key
    c.note(60, False)
    c.note(61, True, 10)
    c.note(12, True, 50)                            # outside the 88
    r = c.report()
    assert r["keys_heard"] == 2 and r["stuck"] == ["C#4"]
    assert (r["velocity_min"], r["velocity_max"]) == (10, 90)
    assert r["out_of_range"] == [12] and r["pass"] is False


def test_group_missing_collapses_runs():
    assert smoke.group_missing([21, 22, 23, 60, 62, 63]) == "A0..B0, C4, D4..D#4"
    assert smoke.group_missing([]) == ""


class _Msg:
    def __init__(self, type, note=0, velocity=0, control=0, value=0):
        self.type, self.note, self.velocity = type, note, velocity
        self.control, self.value = control, value


class _Port:
    def __init__(self, msgs):
        self.batches = [msgs[i:i + 7] for i in range(0, len(msgs), 7)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_pending(self):
        return iter(self.batches.pop(0)) if self.batches else iter(())


def _fake_mido(msgs, ports=("Midi Through Port-0", "CASIO USB-MIDI")):
    m = types.ModuleType("mido")
    m.get_input_names = lambda: list(ports)
    m.opened = []

    def open_input(name):
        m.opened.append(name)
        return _Port(msgs)
    m.open_input = open_input
    return m


def _args(tmp_path):
    return types.SimpleNamespace(n=176, order="GRB", brightness=0.15, dry_run=True,
                                 timeout=5.0, home=str(tmp_path), yes="y")


def test_key_step_all_88_with_pedal_passes_and_lights_each_key(tmp_path, monkeypatch):
    msgs = [_Msg("control_change", control=64, value=127)]
    for p in range(21, 109):
        msgs += [_Msg("note_on", p, 64), _Msg("note_on", p, 0)]
    fake = _fake_mido(msgs)
    monkeypatch.setitem(sys.modules, "mido", fake)
    args = _args(tmp_path)
    strip = smoke.open_strip(args)
    rep = smoke.step_keys(args, strip)
    assert fake.opened == ["CASIO USB-MIDI"], "the ALSA loopback must be skipped"
    assert rep["pass"] and rep["keys_heard"] == 88 and rep["pedal_seen"]
    assert rep["latency_ms_median"] is not None
    assert strip.spi.frames == 176                  # one show per note-on and note-off


def test_key_step_reports_dead_and_stuck_keys(tmp_path, monkeypatch):
    msgs = [_Msg("note_on", p, 64) for p in range(21, 108)]     # C8 dead, none released
    monkeypatch.setitem(sys.modules, "mido", _fake_mido(msgs))
    args = _args(tmp_path)
    args.timeout = 0.3
    rep = smoke.step_keys(args, None)
    assert not rep["pass"] and rep["keys_missing"] == ["C8"] and len(rep["stuck"]) == 87


def test_whole_run_writes_a_report(tmp_path, monkeypatch):
    msgs = []
    for p in range(21, 109):
        msgs += [_Msg("note_on", p, 64), _Msg("note_off", p, 0)]
    monkeypatch.setitem(sys.modules, "mido", _fake_mido(msgs))
    code = smoke.main(["--skip-preflight", "--dry-run", "--yes", "y",
                       "--home", str(tmp_path), "--timeout", "5"])
    import json
    rep = json.loads((tmp_path / "smoke-report.json").read_text(encoding="utf-8"))
    assert code == 0 and rep["pass"] and set(rep["steps"]) == {"strip", "keys"}
