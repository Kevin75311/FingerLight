"""App engine + server tests.  PC-only: no Pi, no strip, no piano.

Driven by the real contract examples and the compiled demo scores, with a fake clock
so the play-along timing is exact rather than slept for.
"""

import json
import os
import sys
import threading
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
REPO = os.path.dirname(APP)
sys.path.insert(0, os.path.join(REPO, "firmware", "src"))
sys.path.insert(0, APP)

from fingerlight.lightscore import LightScoreError  # noqa: E402
from fingerlight.notemap import note_to_leds  # noqa: E402

import engine as E  # noqa: E402

EXAMPLES = os.path.join(REPO, "lightscore", "examples")
DEMO = os.path.join(APP, "demo_scores")
SCALE = "examples/c-major-scale-rh"
CHORDS = "examples/two-hand-chords"


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class RecordingSink:
    def __init__(self):
        self.frames = []

    def describe(self):
        return "recording"

    def show(self, pixels, brightness):
        self.frames.append((dict(pixels), brightness))

    def close(self):
        pass


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def eng(tmp_path, clock):
    sink = RecordingSink()
    e = E.Engine(str(tmp_path), [("examples", EXAMPLES), ("demo", DEMO)],
                 led_sink=sink, clock=clock)
    return e


def play_event(eng, release=True):
    want = sorted(eng.attempt.session.waiting_for)
    for p in want:
        eng.note(p, True)
    if release:
        for p in want:
            eng.note(p, False)
    return want


# ------------------------------------------------------------------ library


def test_library_lists_examples_and_demos(eng):
    ids = {e.id: e for e in eng.library}
    assert SCALE in ids and CHORDS in ids
    assert "demo/grand-staff" in ids and "demo/hard" in ids
    assert ids[SCALE].hands == "R" and ids[CHORDS].hands == "L+R"
    assert ids[SCALE].events == 8 and ids[CHORDS].events == 4


def test_uncompiled_and_broken_files_are_listed_not_hidden(tmp_path, clock):
    d = tmp_path / "scores"
    d.mkdir()
    (d / "etude.musicxml").write_text("<score/>", encoding="utf-8")
    (d / "broken.lightscore.json").write_text('{"format": "lightscore"}', encoding="utf-8")
    e = E.Engine(str(tmp_path), [("scores", str(d))], clock=clock)
    by = {x.title if x.status != "ready" else x.id: x for x in e.library}
    assert by["etude.musicxml"].status == "source"
    assert by["broken.lightscore.json"].status == "invalid"
    with pytest.raises(LightScoreError):
        e.open("scores/broken")


# ------------------------------------------------------------------ wait mode


def test_wait_mode_clean_run_finishes_with_100_and_writes_history(eng, tmp_path):
    eng.open(SCALE)
    for _ in range(8):
        play_event(eng)
    s = eng.summary
    assert s is not None and s["accuracy"] == 100
    assert (s["credited"], s["wrong"], s["missed"]) == (8, 0, 0)
    assert s["problems"] == [] and s["delta"] is None
    hist = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert hist[SCALE][0]["accuracy"] == 100


def test_wrong_key_is_counted_located_and_compared_to_last_attempt(eng):
    eng.open(SCALE)
    for _ in range(8):
        play_event(eng)
    eng.retry()
    eng.note(61, True)                      # C#4 instead of C4, at event 0
    eng.note(61, False)
    for _ in range(8):
        play_event(eng)
    s = eng.summary
    assert s["attempt"] == 2
    assert (s["credited"], s["wrong"]) == (8, 1)
    assert s["accuracy"] == round(100 * 8 / 9)
    assert s["delta"] == s["accuracy"] - 100
    wrong = [p for p in s["problems"] if p["kind"] == "wrong key"]
    assert len(wrong) == 1 and wrong[0]["event"] == 0 and wrong[0]["detail"] == "played C#4"


def test_hesitation_needs_both_the_floor_and_the_median(eng, clock):
    eng.open(SCALE)
    for i in range(8):
        clock.t += 6.0 if i == 4 else 0.5   # one event takes 6 s, the rest 0.5 s
        play_event(eng)
    s = eng.summary
    hes = [p for p in s["problems"] if p["kind"] == "hesitated"]
    assert s["hesitations"] == 1 and hes[0]["event"] == 4


def test_chords_advance_as_a_unit_and_preview_counts_chips(eng):
    eng.open(CHORDS, hands="both")
    st = eng.state()["practice"]
    assert len(st["now"]) == 5 and all(c["waiting"] for c in st["now"])
    first = st["now"][0]["pitch"]
    eng.note(first, True)
    st = eng.state()["practice"]
    assert st["index"] == 0
    assert [c["done"] for c in st["now"]].count(True) == 1


def test_hand_filter(eng):
    eng.open(CHORDS, hands="L")
    hands = {n.h for n in eng.attempt.score.notes}
    assert hands == {"L"}
    with pytest.raises(LightScoreError):
        eng.open(SCALE, hands="L")          # the scale is right hand only


# --------------------------------------------------------------------- loops


def test_loop_button_three_taps(eng):
    eng.open(SCALE)
    play_event(eng)                         # now at event 1
    eng.transport("loop")                   # A = 1
    assert eng.attempt.loop_pending == 1 and eng.attempt.loop is None
    play_event(eng)
    play_event(eng)                         # now at event 3
    eng.transport("loop")                   # B = 3: loop 1..3, cursor back to 1
    assert eng.attempt.loop == (1, 3) and eng.attempt.session.index == 1
    for _ in range(3):
        play_event(eng)
    assert eng.attempt.session.index == 1 and eng.attempt.loop_passes == 1
    eng.transport("loop")                   # clear
    assert eng.attempt.loop is None


def test_loop_spot_retry_starts_inside_the_loop(eng):
    eng.open(SCALE)
    eng.retry(loop=(5, 99))
    a = eng.attempt
    assert a.loop == (5, 7) and a.session.index == 5


# ---------------------------------------------------------------- play-along


def test_play_along_marks_unplayed_events_missed_and_finishes(eng, clock):
    eng.set_setting("mode", "along")
    eng.open(SCALE)
    a = eng.attempt
    assert not a.running, "play-along must wait for the play button"
    eng.transport("play")
    clock.t += 1.0                          # the lead-in
    eng.tick()
    assert a.session.index == 0
    play_event(eng)                         # event 0 on time
    clock.t += 30.0                         # then sit there
    eng.tick()
    s = eng.summary
    assert s is not None and s["credited"] == 1 and s["missed"] == 7
    assert s["accuracy"] == round(100 / 8)
    assert len([p for p in s["problems"] if p["kind"] == "missed"]) == 7


def test_play_along_tempo_scales_the_clock(eng, clock):
    eng.set_setting("mode", "along")
    eng.open(SCALE)
    eng.transport("tempo", -50)             # 50 %
    eng.transport("play")
    a = eng.attempt
    assert a.clock_ms == pytest.approx(-500.0), "lead-in is one wall second at any tempo"
    clock.t += 0.5                          # 250 ms of score time at 50 %
    eng.tick()
    assert a.clock_ms == pytest.approx(-250.0)


def test_player_ahead_of_the_clock_is_not_held_back(eng, clock):
    eng.set_setting("mode", "along")
    eng.open(SCALE)
    eng.transport("play")
    for _ in range(3):
        play_event(eng)
    eng.tick()
    assert eng.attempt.session.index == 3 and eng.attempt.missed == 0


# ------------------------------------------------------------------- outputs


def test_leds_follow_the_cursor_and_stay_on_the_strip(eng, clock):
    sink = eng.led_sink
    eng.open(SCALE)
    px, _ = sink.frames[-1]
    lo, hi = note_to_leds(60, eng.strip)
    assert lo in px and hi in px
    for _ in range(8):
        play_event(eng)
    assert eng.bar_owner() == "free"
    clock.t += 1.0                          # the last key's free-play fade runs out
    eng.tick()
    assert sink.frames[-1][0] == {}, "a finished attempt must blank the bar"
    for frame, _ in sink.frames:
        assert all(0 <= i < eng.strip.led_count for i in frame)


def test_brightness_resends_the_frame(eng):
    eng.open(SCALE)
    n = len(eng.led_sink.frames)
    eng.set_setting("brightness", 0.8)
    assert len(eng.led_sink.frames) == n + 1 and eng.led_sink.frames[-1][1] == 0.8


def test_settings_validate_and_persist(eng, tmp_path, clock):
    eng.set_setting("palette", "distinct")
    eng.set_setting("brightness", 7)        # clamped
    with pytest.raises(ValueError):
        eng.set_setting("palette", "rainbow")
    with pytest.raises(ValueError):
        eng.set_setting("nope", 1)
    again = E.Engine(str(tmp_path), [("examples", EXAMPLES)], clock=clock)
    assert again.settings["palette"] == "distinct" and again.settings["brightness"] == 1.0


def test_state_is_json_and_palette_matches_firmware(eng):
    eng.open(CHORDS)
    st = json.loads(json.dumps(eng.state()))
    from fingerlight import colors
    assert st["palettes"]["ramp"][0] == E.hex_rgb(colors.PALETTE_RAMP[1])
    assert st["practice"]["total"] == 4


def test_beat_at_follows_tempo_changes():
    tempo = [{"t": 0, "bpm": 120}, {"t": 2000, "bpm": 60}]
    assert E.beat_at(2000, tempo) == pytest.approx(4.0)
    assert E.beat_at(3000, tempo) == pytest.approx(5.0)
    assert E.beat_at(1000, []) is None


# --------------------------------------------------------------------- server


@pytest.fixture
def server(tmp_path):
    import server as S
    eng = E.Engine(str(tmp_path), [("examples", EXAMPLES)])
    S.Handler.engine = eng
    srv = S.Server(("127.0.0.1", 0), S.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.stopping = True
    srv.shutdown()
    srv.server_close()


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_server_serves_app_and_drives_an_attempt(server):
    with urllib.request.urlopen(server + "/", timeout=5) as r:
        html = r.read().decode("utf-8")
    assert '<meta charset="utf-8">' in html[:1024]
    for asset in ("/static/app.js", "/static/app.css", "/preview", "/static/preview.js",
                  "/api/geometry"):
        with urllib.request.urlopen(server + asset, timeout=5) as r:
            assert r.status == 200
    assert _post(server, "/api/open", {"id": SCALE})[0] == 200
    assert _post(server, "/api/note", {"pitch": 60, "on": True})[0] == 200
    with urllib.request.urlopen(server + "/api/state", timeout=5) as r:
        st = json.loads(r.read())
    assert st["practice"]["index"] == 1
    code, body = _post(server, "/api/open", {"id": "examples/nope"})
    assert code == 400 and not body["ok"]


@pytest.mark.parametrize("path,body", [
    ("/api/note", {"pitch": 200, "on": True}),
    ("/api/note", {"pitch": "C4"}),
    ("/api/note", [1]),
    ("/api/open", [1]),
    ("/api/settings", [1]),
    ("/api/transport", {"cmd": "tempo", "arg": "fast"}),
    ("/api/settings", {"lookahead": None}),
    ("/api/settings", {"fp_color": "red"}),
    ("/api/settings", {"fp_style": "strobe"}),
    ("/api/note", {"pitch": 60, "on": True, "velocity": 200}),
    ("/api/calibrate", {"cmd": "nudge", "arg": 1}),       # not open
    ("/api/calibrate", {"arg": 1}),
    ("/api/pedal", {}),
])
def test_server_rejects_bad_bodies_with_400(server, path, body):
    """Found by the verifier: [1] crashed the handler with no response at all."""
    _post(server, "/api/open", {"id": SCALE})
    code, reply = _post(server, path, body)
    assert code == 400 and reply["ok"] is False


def test_server_refuses_paths_outside_static(server):
    for bad in ("/static/../server.py", "/static/..%2fserver.py"):
        try:
            urllib.request.urlopen(server + bad, timeout=5)
            raise AssertionError("served %s" % bad)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404


def test_skipped_events_are_reported_not_hidden(eng):
    eng.open(SCALE)
    play_event(eng)
    eng.transport("next")
    for _ in range(6):
        play_event(eng)
    s = eng.summary
    assert (s["credited"], s["skipped"]) == (7, 1)
    assert [p["event"] for p in s["problems"] if p["kind"] == "skipped"] == [1]


# ------------------------------------------------------------------ free play


def test_free_play_owns_the_bar_when_no_attempt_is_live(eng, clock):
    sink = eng.led_sink
    assert eng.bar_owner() == "free"
    eng.set_setting("fp_color", "#102030")
    eng.note(60, True, velocity=90)
    lo, hi = note_to_leds(60, eng.strip)
    assert sink.frames[-1][0] == {lo: (16, 32, 48), hi: (16, 32, 48)}
    st = eng.state()
    assert st["bar"] == "free" and st["freeplay"]["held"] == ["C4"]
    eng.note(60, False)
    clock.t += 0.2                          # half of the default 400 ms fade
    eng.tick()
    assert sink.frames[-1][0][lo] == (4, 8, 12)
    v = eng.version
    clock.t += 0.3
    eng.tick()
    assert sink.frames[-1][0] == {} and eng.version == v + 1, "dark: one state push"
    eng.tick()
    assert eng.version == v + 1, "nothing lit: ticks are free"


def test_fade_steps_drive_the_leds_without_flooding_the_screens(eng, clock):
    eng.note(60, True)
    eng.note(60, False)
    v, n = eng.version, len(eng.led_sink.frames)
    for _ in range(5):
        clock.t += 0.04
        eng.tick()
    assert eng.version == v and len(eng.led_sink.frames) == n + 5


def test_pedal_holds_free_play_lights(eng, clock):
    eng.pedal(True)
    eng.note(64, True)
    eng.note(64, False)
    clock.t += 5.0
    eng.tick()
    assert eng.led_sink.frames[-1][0], "held by the pedal"
    assert eng.state()["freeplay"]["pedal"] is True
    eng.pedal(False)
    clock.t += 1.0
    eng.tick()
    assert eng.led_sink.frames[-1][0] == {}


def test_a_live_attempt_takes_the_bar_and_free_play_gets_it_back(eng):
    eng.open(SCALE)
    assert eng.bar_owner() == "practice"
    eng.end_attempt()
    assert eng.bar_owner() == "free"
    assert eng.summary["stopped"] is True


def test_a_stopped_attempt_stays_stopped(eng):
    """Before: a key after Stop un-paused a summarised attempt in wait mode."""
    eng.open(SCALE)
    play_event(eng)
    eng.transport("stop")
    summary = eng.summary
    eng.note(62, True)                      # the next scale note
    eng.note(62, False)
    assert eng.attempt.session.index == 1 and eng.summary is summary
    with pytest.raises(LightScoreError):
        eng.transport("stop")               # a second stop would write a second row
    assert len(eng.history[SCALE]) == 1
    assert eng.state()["practice"]["ended"] is True


def test_free_play_settings_validate_and_persist(eng, tmp_path, clock):
    eng.set_setting("fp_style", "split")
    eng.set_setting("fp_color2", "#ABCDEF")
    eng.set_setting("fp_split", 500)
    eng.set_setting("fp_sustain", "false")
    assert eng.settings["fp_color2"] == "#abcdef"
    assert eng.settings["fp_split"] == 108
    assert eng.settings["fp_sustain"] is False
    for k, v in (("fp_style", "strobe"), ("fp_color", "#12345"), ("fp_sustain", "maybe")):
        with pytest.raises(ValueError):
            eng.set_setting(k, v)
    again = E.Engine(str(tmp_path), [], clock=clock)
    assert again.settings["fp_style"] == "split" and again._look.color2 == (171, 205, 239)


# ---------------------------------------------------------------- calibration


def test_calibrate_select_by_key_nudge_save_and_reload(eng, tmp_path, clock):
    base = note_to_leds(64, eng.strip)
    eng.calibrate("start")
    assert eng.bar_owner() == "calibrate"
    eng.note(64, True)                      # pressing a key picks it, nothing else
    eng.note(64, False)
    c = eng.state()["calibration"]
    assert c["selected"] == 64 and c["note"] == "E4" and 64 in c["visited"]
    eng.calibrate("nudge", 1)
    eng.calibrate("nudge", 1)
    c = eng.state()["calibration"]
    assert c["offset"] == 2 and c["offset_mm"] == 13.9 and c["dirty"]
    px = eng.led_sink.frames[-1][0]
    assert px[base[0] + 2] == E.CAL_SELECTED and px[base[1] + 2] == E.CAL_SELECTED
    eng.calibrate("save")
    assert not eng.state()["calibration"]["dirty"]
    saved = json.loads((tmp_path / "led_offsets.json").read_text(encoding="utf-8"))
    assert saved["format"] == E.OFFSETS_FORMAT and saved["offsets"] == {"64": 2}
    eng.calibrate("close")
    assert eng.bar_owner() == "free"
    assert note_to_leds(64, eng.strip) == (base[0] + 2, base[1] + 2)
    again = E.Engine(str(tmp_path), [], clock=clock)      # the saved map is the default
    assert note_to_leds(64, again.strip) == (base[0] + 2, base[1] + 2)
    assert again.state()["led_offsets"]["count"] == 1


def test_close_without_save_drops_the_draft(eng):
    base = note_to_leds(60, eng.strip)
    eng.calibrate("start", 60)
    eng.calibrate("nudge", -1)
    assert note_to_leds(60, eng.strip)[0] == base[0] - 1
    eng.calibrate("close")
    assert note_to_leds(60, eng.strip) == base
    assert eng.offsets == {}


def test_shift_all_reset_and_limits(eng):
    eng.calibrate("start", 60)
    eng.calibrate("shift_all", 1)
    assert len(eng.calib.draft) == 88
    eng.calibrate("reset_key")
    assert 60 not in eng.calib.draft and len(eng.calib.draft) == 87
    eng.calibrate("reset_all")
    assert eng.calib.draft == {}
    for _ in range(E.CAL_MAX):
        eng.calibrate("nudge", 1)
    with pytest.raises(ValueError):
        eng.calibrate("nudge", 1)
    with pytest.raises(ValueError):
        eng.calibrate("nudge", 3)
    eng.calibrate("select", 108)
    with pytest.raises(ValueError, match="leave the strip"):
        for _ in range(E.CAL_MAX):
            eng.calibrate("nudge", 1)
    with pytest.raises(ValueError):
        eng.calibrate("bogus")


def test_calibration_pauses_a_running_attempt_and_shows_its_new_map(eng):
    eng.open(SCALE)
    play_event(eng)
    assert eng.attempt.running
    eng.calibrate("start", 62)
    assert not eng.attempt.running
    eng.note(64, True)                      # selects, does not play the score
    assert eng.attempt.session.index == 1
    eng.calibrate("nudge", 1)
    eng.calibrate("save")
    eng.calibrate("close")
    assert eng.bar_owner() == "practice"
    assert eng.attempt.session.settings.offset_for(64) == 1


def test_bad_offsets_file_is_ignored(tmp_path, clock):
    (tmp_path / "led_offsets.json").write_text(json.dumps({
        "format": E.OFFSETS_FORMAT,
        "offsets": {"60": 1, "61": 99, "7": 1, "x": 2, "62": "bad"}}), encoding="utf-8")
    e = E.Engine(str(tmp_path), [], clock=clock)
    assert e.offsets == {60: 1}


def test_server_free_play_and_calibration_round_trip(server):
    assert _post(server, "/api/pedal", {"down": True})[0] == 200
    assert _post(server, "/api/note", {"pitch": 60, "on": True, "velocity": 30})[0] == 200
    assert _post(server, "/api/open", {"id": SCALE})[0] == 200
    assert _post(server, "/api/freeplay", {})[0] == 200
    assert _post(server, "/api/calibrate", {"cmd": "start"})[0] == 200
    assert _post(server, "/api/calibrate", {"cmd": "nudge", "arg": 1})[0] == 200
    with urllib.request.urlopen(server + "/api/state", timeout=5) as r:
        st = json.loads(r.read())
    assert st["bar"] == "calibrate" and st["calibration"]["offset"] == 1
    assert st["freeplay"]["pedal"] is True and st["summary"]["stopped"] is True
    assert _post(server, "/api/calibrate", {"cmd": "close"})[0] == 200


def test_led_mirror_is_a_saved_setting_and_reaches_the_sink(tmp_path, clock):
    import leds

    assert E.Engine(str(tmp_path), [], clock=clock).settings["led_mirror"] is False
    (tmp_path / "settings.json").write_text(json.dumps({"led_mirror": True}), encoding="utf-8")
    e = E.Engine(str(tmp_path), [], clock=clock)
    assert e.settings["led_mirror"] is True
    sink = leds.make_sink("dry", e.strip.led_count, "GRB", "1110", e.settings["led_mirror"])
    assert sink.strip.mirror is True
