"""Play a light-score through the firmware and watch the bar do it, live.

    python sim/play.py                                   # default score, live viewer
    python sim/play.py --score lightscore/examples/two-hand-chords.lightscore.json
    python sim/play.py --speed 0.5 --loop                 # half speed, repeating
    python sim/play.py --selftest                         # headless, asserts, no window
    python sim/play.py --frames sim/renders/anim --fps 12 # offscreen PNG sequence

Nothing about the light is simulated here. The score is parsed by
`fingerlight.lightscore`, the cursor is a real `fingerlight.practice.PracticeSession`,
and every frame is that session's own `frame().pixels` -- LED index -> RGB, the dict
the Pi would push to the strip. This file only decides *when* the virtual player
hits each note and copies the resulting pixels onto the scene's `led_NNN` geoms.

The virtual player is deliberately perfect: it plays each note exactly at its
onset and releases it at its end, so what you see is the score as written. Pass
`--miss N` to make it fumble every Nth event instead (plays a neighbouring
semitone first), which is what exercises the wrong-note path in the state
machine.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCENE = os.path.join(HERE, "scene.xml")
DEFAULT_SCORE = os.path.join(ROOT, "lightscore", "examples",
                             "c-major-scale-rh.lightscore.json")
sys.path.insert(0, os.path.join(ROOT, "firmware", "src"))
sys.path.insert(0, HERE)

OFF = (0.03, 0.03, 0.035, 1.0)


def load_session(score_path, preview, palette):
    from fingerlight import lightscore, notemap, practice

    score = lightscore.load(score_path)
    settings = notemap.StripSettings.load()
    session = practice.PracticeSession(score=score, settings=settings,
                                       preview=preview, palette=palette)
    return score, session, settings


def led_geom_ids(model):
    """geom id per LED index, so the hot loop is an array write, not a name lookup."""
    import mujoco

    ids = {}
    for gid in range(model.ngeom):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, gid)
        if name and name.startswith("led_"):
            ids[int(name[4:])] = gid
    return ids


def schedule(score, miss=0):
    """(time_ms, kind, pitch, note) events for the virtual player, in order.

    kind is "on" or "off"; a fumbled event gets an extra wrong "on" a semitone
    below, 60 ms early, which the practice session should reject and count.
    """
    out = []
    for i, ev in enumerate(score.events):
        if miss and i and i % miss == 0:
            wrong = max(21, ev.notes[0].p - 1)
            out.append((ev.t - 60, "on", wrong, None))
            out.append((ev.t - 20, "off", wrong, None))
        for n in ev.notes:
            out.append((n.t, "on", n.p, n))
            out.append((n.end, "off", n.p, n))
    out.sort(key=lambda e: (e[0], e[1] == "on"))
    return out


def apply_pixels(model, ids, pixels):
    """Copy a frame's {led: rgb} onto the scene. Returns how many geoms are lit."""
    for gid in ids.values():
        model.geom_rgba[gid] = OFF
    lit = 0
    for led, rgb in pixels.items():
        gid = ids.get(led)
        if gid is None:
            continue
        model.geom_rgba[gid] = [rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0]
        lit += 1
    return lit


class Player:
    """Drives one pass of the score. `advance(t_ms)` applies everything due."""

    def __init__(self, score, session, miss=0, log=None):
        self.session = session
        self.events = schedule(score, miss)
        self.i = 0
        self.log = log
        self.rejected = 0

    @property
    def done(self):
        return self.i >= len(self.events) and self.session.finished

    def advance(self, t_ms):
        fired = 0
        while self.i < len(self.events) and self.events[self.i][0] <= t_ms:
            _, kind, pitch, note = self.events[self.i]
            self.i += 1
            fired += 1
            if kind == "on":
                ok = self.session.note_on(pitch)
                if not ok:
                    self.rejected += 1
                if self.log and note is not None:
                    self.log(t_ms, note, ok)
                elif self.log:
                    self.log(t_ms, None, ok, pitch)
            else:
                self.session.note_off(pitch)
        return fired


def note_line(t_ms, note, ok, pitch=None):
    from fingerlight import notemap

    settings = notemap.StripSettings.load()
    if note is None:
        return "t=%6.2fs  WRONG note %3d            -> %s" % (
            t_ms / 1000.0, pitch, "accepted?!" if ok else "rejected")
    lo, hi = notemap.note_to_leds(note.p, settings)
    return "t=%6.2fs  %-4s %s%d  midi %3d -> LED %3d/%-3d  %s" % (
        t_ms / 1000.0, _name(note.p), note.h, note.f, note.p, lo, hi,
        "ok" if ok else "REJECTED")


_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _name(midi):
    return "%s%d" % (_NAMES[midi % 12], midi // 12 - 1)


def run_live(model, data, ids, score, session, args):
    import mujoco
    import mujoco.viewer

    player = Player(score, session, args.miss)
    printed = []
    with mujoco.viewer.launch_passive(model, data) as viewer:
        while viewer.is_running():
            t0 = time.time()
            player_done = False
            while viewer.is_running() and not player_done:
                t_ms = (time.time() - t0) * 1000.0 * args.speed
                for idx in range(player.i, len(player.events)):
                    if player.events[idx][0] > t_ms:
                        break
                before = player.i
                player.advance(t_ms)
                if player.i != before:
                    for k in range(before, player.i):
                        tm, kind, pitch, note = player.events[k]
                        if kind == "on":
                            line = note_line(tm, note, True, pitch)
                            if line not in printed:
                                print(line, flush=True)
                apply_pixels(model, ids, session.frame().pixels)
                mujoco.mj_forward(model, data)
                viewer.sync()
                time.sleep(1.0 / 60.0)
                player_done = player.done or t_ms > score.duration_ms + 1500
            if not args.loop:
                break
            session.reset()
            player = Player(score, session, args.miss)
            printed = []
    return 0


def run_frames(model, data, ids, score, session, args):
    import mujoco

    from preview import _write_png

    os.makedirs(args.frames, exist_ok=True)
    player = Player(score, session, args.miss)
    renderer = mujoco.Renderer(model, args.height, args.width)
    step_ms = 1000.0 / args.fps
    t_ms, n = 0.0, 0
    total = score.duration_ms + 800
    while t_ms <= total:
        player.advance(t_ms)
        apply_pixels(model, ids, session.frame().pixels)
        mujoco.mj_forward(model, data)
        renderer.update_scene(data, camera=args.camera)
        _write_png(os.path.join(args.frames, "f%04d.png" % n), renderer.render())
        n += 1
        t_ms += step_ms
    print("%d frames -> %s (camera %s, %.0f fps, %.1f s of score)"
          % (n, args.frames, args.camera, args.fps, total / 1000.0))
    return 0


def run_selftest(model, data, ids, score, session, args):
    """Headless pass with assertions -- the evidence that playback works.

    Checks that each event's own LEDs are lit while it is current, that the
    cursor reaches the end, and that the lit set actually changes over time (a
    still picture would pass every per-frame check individually).
    """
    import mujoco
    from fingerlight import notemap

    settings = notemap.StripSettings.load()
    player = Player(score, session, args.miss)
    step_ms = 10.0
    t_ms = 0.0
    seen_sets = set()
    bad = []
    checked = 0
    while t_ms <= score.duration_ms + 500:
        player.advance(t_ms)
        frame = session.frame()
        lit = apply_pixels(model, ids, frame.pixels)
        mujoco.mj_forward(model, data)
        seen_sets.add(frozenset(frame.pixels))
        if not frame.finished:
            ev = score.events[frame.index]
            want = set()
            for note in ev.notes:
                want.update(notemap.note_to_leds(note.p, settings))
            missing = want - set(frame.pixels)
            if missing:
                bad.append((t_ms, frame.index, sorted(missing)))
            checked += 1
        t_ms += step_ms

    events_done, total = session.progress()
    print("score      %s" % os.path.relpath(args.score, ROOT).replace("\\", "/"))
    print("           %d notes in %d events, %.2f s"
          % (len(score.notes), len(score.events), score.duration_ms / 1000.0))
    print("cursor     %d/%d events completed, finished=%s, wrong notes rejected %d"
          % (events_done, total, session.finished, player.rejected))
    print("frames     %d sampled at %.0f Hz, %d distinct lit-sets"
          % (checked, 1000.0 / step_ms, len(seen_sets)))
    print("current-event LEDs missing from the frame: %d" % len(bad))
    if bad:
        for t, i, m in bad[:5]:
            print("   t=%.2fs event %d missing %s" % (t / 1000.0, i, m))
    ok = (session.finished and not bad and len(seen_sets) > len(score.events) // 2
          and lit >= 0)
    print("\nSELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default=SCENE)
    ap.add_argument("--score", default=DEFAULT_SCORE)
    ap.add_argument("--speed", type=float, default=1.0, help="playback rate, 1.0 = written tempo")
    ap.add_argument("--preview", type=int, default=1, help="how many future events to dim-light")
    ap.add_argument("--palette", default=None, help="ramp | distinct | anchors")
    ap.add_argument("--miss", type=int, default=0,
                    help="fumble every Nth event (0 = play it perfectly)")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="headless, with assertions")
    ap.add_argument("--frames", default=None, help="write a PNG sequence to this directory")
    ap.add_argument("--fps", type=float, default=12.0)
    ap.add_argument("--camera", default="bar_closeup")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    args = ap.parse_args()

    import mujoco

    model = mujoco.MjModel.from_xml_path(args.scene)
    data = mujoco.MjData(model)
    ids = led_geom_ids(model)
    if not ids:
        raise SystemExit("no led_* geoms in %s -- run sim/make_scene.py first" % args.scene)
    score, session, _ = load_session(args.score, args.preview, args.palette)

    if args.selftest:
        return run_selftest(model, data, ids, score, session, args)
    if args.frames:
        return run_frames(model, data, ids, score, session, args)
    print("playing %s (%d events, %.2f s) at %.2fx -- close the window to stop"
          % (os.path.basename(args.score), len(score.events),
             score.duration_ms / 1000.0, args.speed))
    return run_live(model, data, ids, score, session, args)


if __name__ == "__main__":
    sys.exit(main())
