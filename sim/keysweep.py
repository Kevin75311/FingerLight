"""Press every key A0..C8 in turn and light the LEDs that key owns.

    python sim/keysweep.py                    # live viewer, walks the keyboard
    python sim/keysweep.py --selftest         # headless, asserts every step
    python sim/keysweep.py --frames sim/renders/sweep --fps 12
    python sim/keysweep.py --notes C4,E4,G4 --hold 600

This is the end-to-end check of the whole cue path in one loop: for each MIDI
note it commands that key's actuator down, asks the *firmware*
(`fingerlight.notemap.note_to_leds` + `fingerlight.colors.pair_colors`) which
LEDs to light and in what colour, and lets MuJoCo step. Nothing here knows which
LED belongs to which key -- if the bar lights the wrong place, the map is wrong.

`--selftest` is the version with teeth. Per note it asserts, from the stepped
model rather than from intent:

  * the key that moved is the one named for that note, and no other key moved;
  * its front tip actually dipped, within tolerance of the 10 mm design travel;
  * exactly the LEDs the firmware named are lit, and they are the ones physically
    nearest that key on the bar.
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
sys.path.insert(0, os.path.join(ROOT, "firmware", "src"))
sys.path.insert(0, HERE)

OFF = (0.03, 0.03, 0.035, 1.0)
MIDI_LOW, MIDI_HIGH = 21, 108
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Which finger the sweep pretends to use, so the colours vary down the keyboard.
FINGERS = [(h, f) for h in ("L", "R") for f in (1, 2, 3, 4, 5)]


def note_name(midi):
    return "%s%d" % (NAMES[midi % 12], midi // 12 - 1)


def parse_note(text):
    import re

    m = re.match(r"^([A-Ga-g])([#b]?)(-?\d)$", text.strip())
    if not m:
        raise SystemExit("bad note %r" % text)
    semi = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1).upper()]
    acc = 1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0
    return (int(m.group(3)) + 1) * 12 + semi + acc


class Rig:
    """The scene plus the lookups the sweep needs, resolved once."""

    def __init__(self, scene_path):
        import mujoco

        self.mj = mujoco
        self.model = mujoco.MjModel.from_xml_path(scene_path)
        self.data = mujoco.MjData(self.model)
        self.led_gid = {}
        for gid in range(self.model.ngeom):
            n = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, gid)
            if n and n.startswith("led_"):
                self.led_gid[int(n[4:])] = gid
        self.diff_gid = {}
        self.wall_gid = []
        for gid in range(self.model.ngeom):
            n = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, gid)
            if n and n.startswith("diff_"):
                self.diff_gid[int(n[5:])] = gid
            elif n and n.startswith("diffwall_"):
                self.wall_gid.append(gid)
        self.act = {}
        self.jnt = {}
        for midi in range(MIDI_LOW, MIDI_HIGH + 1):
            body = "key_" + note_name(midi).replace("#", "s")
            a = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, "act_" + body)
            j = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "j_" + body)
            if a < 0 or j < 0:
                raise SystemExit("scene has no hinge/actuator for %s -- re-run make_scene.py"
                                 % body)
            self.act[midi] = a
            self.jnt[midi] = j
        self.qadr = {m: self.model.jnt_qposadr[j] for m, j in self.jnt.items()}
        self.geom_of = {}
        for midi in range(MIDI_LOW, MIDI_HIGH + 1):
            body = "key_" + note_name(midi).replace("#", "s")
            bid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
            self.geom_of[midi] = self.model.body_geomadr[bid]
        from fingerlight import notemap

        self.settings = notemap.StripSettings.load()
        mujoco.mj_forward(self.model, self.data)
        # Pick the front-tip vertices ONCE, at rest, and follow those same ones.
        # Re-selecting "whatever is within 2 mm of the front" on every sample is
        # not a measurement of motion: rotation slides the key back by ~0.4 mm,
        # a different subset of a boxy mesh falls in the window, and the mean
        # jumps. That read as a black key dipping 6.45 mm when its hinge had
        # turned 97.7% of a travel worth 9.6 mm.
        self.tip_idx = {}
        for midi in self.act:
            w = self.verts(self.geom_of[midi])
            self.tip_idx[midi] = np.nonzero(w[:, 1] < w[:, 1].min() + 2.0)[0]
        self.rest_tip = {m: self.tip_z(m) for m in self.act}
        # The light-field model works in the DESIGN frame (x = 0 at A0's left
        # edge, the frame data/led_map.json is written in); the scene is in world
        # millimetres. Sampling the field at world x put the glow ~613 mm away,
        # off the end of the bar, and the diffuser rendered dark everywhere.
        import json

        with open(os.path.join(HERE, "assets", "keymap.json"), "rb") as f:
            self.a0_left_x = float(json.load(f)["measured_from_cad"]["a0_left_x"])
        self.diff_x = [0.0] * (max(self.diff_gid) + 1 if self.diff_gid else 0)
        for i, gid in self.diff_gid.items():
            self.diff_x[i] = float(self.data.geom_xpos[gid][0] * 1000) - self.a0_left_x

    # ---------------------------------------------------------------- measuring

    def verts(self, gid):
        m, d = self.model, self.data
        mid = m.geom_dataid[gid]
        adr, num = m.mesh_vertadr[mid], m.mesh_vertnum[mid]
        v = m.mesh_vert[adr:adr + num].astype(np.float64)
        return (v @ d.geom_xmat[gid].reshape(3, 3).T + d.geom_xpos[gid]) * 1000

    def tip_z(self, midi):
        """Height of the key's front tip, mm -- what a finger would feel move.

        Averages the vertex set chosen at rest (see `tip_idx`), never a freshly
        re-selected one.
        """
        w = self.verts(self.geom_of[midi])
        return float(w[self.tip_idx[midi], 2].mean())

    def led_x(self, led):
        w = self.verts(self.led_gid[led])
        return float((w[:, 0].min() + w[:, 0].max()) / 2.0)

    def key_tail_x(self, midi):
        w = self.verts(self.geom_of[midi])
        rear = w[w[:, 1] > w[:, 1].max() - 20.0]
        return float((rear[:, 0].min() + rear[:, 0].max()) / 2.0)

    # ----------------------------------------------------------------- driving

    def press(self, midi, depth=1.0):
        """Command one key down (depth 0..1 of its own range), release the rest."""
        self.data.ctrl[:] = 0.0
        lo, hi = self.model.actuator_ctrlrange[self.act[midi]]
        self.data.ctrl[self.act[midi]] = hi * depth

    def release_all(self):
        self.data.ctrl[:] = 0.0

    def settle(self, seconds):
        n = max(1, int(seconds / self.model.opt.timestep))
        for _ in range(n):
            self.mj.mj_step(self.model, self.data)

    def light_many(self, midis, mode="weighted", diffuser=True, hand="R", finger=1,
                   palette=None):
        """Light several keys at once -- the case where neighbours share LEDs."""
        for gid in self.led_gid.values():
            self.model.geom_rgba[gid] = OFF
        if mode == "weighted":
            import lightfield as lf

            pixels, w = lf.frame_for(midis)
        else:
            from fingerlight import colors, notemap

            pixels, w = {}, {}
            for m in midis:
                lo, hi = notemap.note_to_leds(m, self.settings)
                h, f = colors.pair_colors(hand, finger, m, palette=palette)
                pixels[lo], pixels[hi] = h, f
                w[lo] = w[hi] = 1.0
        for led, rgb in pixels.items():
            self.model.geom_rgba[self.led_gid[led]] = [rgb[0] / 255.0, rgb[1] / 255.0,
                                                       rgb[2] / 255.0, 1.0]
        self.show_diffuser(w if diffuser else None)
        return pixels, w

    def light(self, midi, hand, finger, palette=None, mode="pair", diffuser=False):
        """Drive the bar for one note. Returns ({led: rgb}, {led: weight}).

        mode="pair"     - what the firmware ships today: the fixed (hand, finger)
                          pixel pair, hand colour and finger colour.
        mode="weighted" - the continuous model in sim/lightfield.py: the key's
                          slot blurred by the diffuser, 2-4 LEDs, one colour for
                          every key so the shapes can be compared.
        """
        for gid in self.led_gid.values():
            self.model.geom_rgba[gid] = OFF
        if mode == "weighted":
            import lightfield as lf

            pixels, w = lf.frame_for([midi])
        else:
            from fingerlight import colors, notemap

            lo, hi = notemap.note_to_leds(midi, self.settings)
            hand_rgb, finger_rgb = colors.pair_colors(hand, finger, midi, palette=palette)
            pixels, w = {lo: hand_rgb, hi: finger_rgb}, {lo: 1.0, hi: 1.0}
        for led, rgb in pixels.items():
            self.model.geom_rgba[self.led_gid[led]] = [rgb[0] / 255.0, rgb[1] / 255.0,
                                                       rgb[2] / 255.0, 1.0]
        self.show_diffuser(w if diffuser else None)
        return pixels, w

    def show_diffuser(self, active):
        """Colour the diffuser surface with the summed light field, or hide it.

        `active` is {led: weight}; None makes every segment invisible so the bare
        LEDs can be compared against the diffused look in the same scene.
        """
        if not self.diff_gid:
            return
        idx = np.array(sorted(self.diff_gid))
        gids = np.array([self.diff_gid[i] for i in idx])
        for g in self.wall_gid:
            self.model.geom_rgba[g][3] = 0.0 if active is None else 1.0
        if active is None:
            self.model.geom_rgba[gids, 3] = 0.0
            return
        import lightfield as lf

        xs = np.array(self.diff_x)[idx]
        rgba = lf.to_display(lf.field(xs, active))
        # a diffuser is never fully dark: unlit profile still reads as grey plastic
        rgba[:, :3] = np.maximum(rgba[:, :3], 0.055)
        self.model.geom_rgba[gids] = rgba

    def lit_leds(self):
        """Read back which LEDs the scene is actually showing as driven.

        The threshold sits just above the unlit colour, not at an arbitrary 0.1:
        the weighted model's dimmest pixel is PWM 16/255 = 0.063, and a 0.1
        threshold silently declared it dark.
        """
        return {led for led, gid in self.led_gid.items()
                if float(self.model.geom_rgba[gid][:3].max()) > OFF[0] + 0.012}


def notes_from(args):
    if args.notes:
        return [parse_note(t) for t in args.notes.split(",") if t.strip()]
    return list(range(max(MIDI_LOW, args.low), min(MIDI_HIGH, args.high) + 1, args.step))


def run_selftest(rig, notes, args):
    """Headless sweep with per-note assertions. Prints a row per failure only."""
    import mujoco

    failures = []
    dips = []
    offsets = []
    for i, midi in enumerate(notes):
        hand, finger = FINGERS[i % len(FINGERS)]
        rig.press(midi)
        rig.settle(args.hold / 1000.0)
        pixels, wts = rig.light(midi, hand, finger, mode=args.light,
                                diffuser=args.diffuser)
        mujoco.mj_forward(rig.model, rig.data)

        # 1. the right key moved, and only that one
        moved = {m: abs(float(rig.data.qpos[rig.qadr[m]])) for m in rig.qadr}
        top = max(moved, key=moved.get)
        others = max((v for m, v in moved.items() if m != midi), default=0.0)
        if top != midi:
            failures.append("%s: %s moved instead" % (note_name(midi), note_name(top)))
        if others > 1e-4:
            failures.append("%s: another key moved by %.2e rad" % (note_name(midi), others))

        # 2. the front tip actually went down, by about the design travel
        dip = rig.rest_tip[midi] - rig.tip_z(midi)
        dips.append(dip)
        if not (args.dip_min <= dip <= args.dip_max):
            failures.append("%s: front tip dipped %.2f mm, want %.1f..%.1f"
                            % (note_name(midi), dip, args.dip_min, args.dip_max))

        # 3. exactly the firmware's LEDs are lit
        lit = rig.lit_leds()
        if lit != set(pixels):
            failures.append("%s: lit %s, firmware asked for %s"
                            % (note_name(midi), sorted(lit), sorted(pixels)))

        # 4. the light is physically over this key. With weights, the honest
        # measure is where the blob's centre of mass lands, not which single
        # pixel is nearest: three LEDs at 0.28/1.00/0.81 put the light between
        # them, which is the entire point of the continuous model.
        tail_x = rig.key_tail_x(midi)
        near = min(rig.led_gid, key=lambda l: abs(rig.led_x(l) - tail_x))
        if args.light == "weighted" and wts:
            tot = sum(wts.values())
            centroid = sum(rig.led_x(l) * w for l, w in wts.items()) / tot
            d = abs(centroid - tail_x)
        else:
            d = min(abs(rig.led_x(l) - tail_x) for l in pixels)
        offsets.append(d)
        if min(abs(l - near) for l in pixels) > 1:
            failures.append("%s: lit LEDs %s but the nearest LED to the key is %d"
                            % (note_name(midi), sorted(pixels), near))
        if d > args.max_offset:
            failures.append("%s: nearest lit LED is %.2f mm from the key centre (limit %.1f)"
                            % (note_name(midi), d, args.max_offset))

        rig.release_all()
        rig.settle(args.release / 1000.0)
        back = rig.rest_tip[midi] - rig.tip_z(midi)
        if abs(back) > args.rest_tol:
            failures.append("%s: did not return, %.2f mm low after release"
                            % (note_name(midi), back))

    dips, offsets = np.array(dips), np.array(offsets)
    # The per-note limit only catches a gross error; a whole-strip misalignment
    # shows up in the MEAN. Measured: 1.55 mm with the map and the tape agreeing,
    # 5.33 mm with the tape moved 1.24 LED pitches (make_scene.py --align map).
    if offsets.mean() > args.max_mean_offset:
        failures.append("mean lit-LED-to-key offset %.2f mm over %d notes (limit %.1f) "
                        "-- the map and the tape disagree, not one note"
                        % (offsets.mean(), len(offsets), args.max_mean_offset))
    print("notes swept        %d (%s .. %s)" % (len(notes), note_name(notes[0]),
                                                note_name(notes[-1])))
    print("front-tip dip      %.2f .. %.2f mm (mean %.2f, design %.1f)"
          % (dips.min(), dips.max(), dips.mean(), args.dip_target))
    print("lit LED to key     %.2f .. %.2f mm (mean %.2f, limits: %.1f each / %.1f mean)"
          % (offsets.min(), offsets.max(), offsets.mean(), args.max_offset,
             args.max_mean_offset))
    print("hold %d ms, release %d ms, timestep %.4f s"
          % (args.hold, args.release, rig.model.opt.timestep))
    print("failures           %d" % len(failures))
    for f in failures[:12]:
        print("   " + f)
    print("\nKEYSWEEP %s" % ("PASS" if not failures else "FAIL"))
    return 0 if not failures else 1


def run_live(rig, notes, args):
    import mujoco.viewer

    print("sweeping %d keys, %d ms each -- close the window to stop"
          % (len(notes), args.hold + args.release))
    with mujoco.viewer.launch_passive(rig.model, rig.data) as viewer:
        while viewer.is_running():
            for i, midi in enumerate(notes):
                if not viewer.is_running():
                    break
                hand, finger = FINGERS[i % len(FINGERS)]
                pixels, _w = rig.light(midi, hand, finger, args.palette,
                                       mode=args.light, diffuser=args.diffuser)
                print("%-4s midi %3d  %s%d -> LED %s"
                      % (note_name(midi), midi, hand, finger,
                         "/".join(str(p) for p in sorted(pixels))), flush=True)
                rig.press(midi)
                self_step(rig, viewer, args.hold / 1000.0)
                rig.release_all()
                self_step(rig, viewer, args.release / 1000.0)
            if not args.loop:
                break
    return 0


def self_step(rig, viewer, seconds):
    """Step for `seconds` of sim time, syncing the window at ~60 Hz."""
    end = rig.data.time + seconds
    while rig.data.time < end and viewer.is_running():
        for _ in range(int(1.0 / 60.0 / rig.model.opt.timestep) or 1):
            rig.mj.mj_step(rig.model, rig.data)
        viewer.sync()
        time.sleep(1.0 / 60.0)


def run_frames(rig, notes, args):
    import mujoco

    from preview import _write_png

    os.makedirs(args.frames, exist_ok=True)
    renderer = mujoco.Renderer(rig.model, args.height, args.width)
    n = 0
    if args.chord:
        chord = [parse_note(t) for t in args.chord.split(",") if t.strip()]
        rig.light_many(chord, mode=args.light, diffuser=args.diffuser)
        for m in chord:
            lo, hi = rig.model.actuator_ctrlrange[rig.act[m]]
            rig.data.ctrl[rig.act[m]] = hi
        rig.settle(args.hold / 1000.0)
        mujoco.mj_forward(rig.model, rig.data)
        renderer.update_scene(rig.data, camera=args.camera)
        _write_png(os.path.join(args.frames, "chord.png"), renderer.render())
        print("chord %s -> %s" % (args.chord, args.frames))
        return 0
    for i, midi in enumerate(notes):
        hand, finger = FINGERS[i % len(FINGERS)]
        rig.light(midi, hand, finger, args.palette, mode=args.light,
                  diffuser=args.diffuser)
        rig.press(midi)
        rig.settle(args.hold / 1000.0)
        mujoco.mj_forward(rig.model, rig.data)
        renderer.update_scene(rig.data, camera=args.camera)
        _write_png(os.path.join(args.frames, "k%04d.png" % n), renderer.render())
        n += 1
        rig.release_all()
        rig.settle(args.release / 1000.0)
    print("%d frames -> %s (camera %s)" % (n, args.frames, args.camera))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default=SCENE)
    ap.add_argument("--notes", default="", help="explicit list, e.g. C4,E4,G4")
    ap.add_argument("--chord", default="", help="light these notes together, one frame")
    ap.add_argument("--low", type=int, default=MIDI_LOW)
    ap.add_argument("--high", type=int, default=MIDI_HIGH)
    ap.add_argument("--step", type=int, default=1, help="press every Nth key")
    ap.add_argument("--hold", type=int, default=150,
                    help="ms held down (measured: 97%% of travel by 50 ms, 100%% by 100)")
    ap.add_argument("--release", type=int, default=150,
                    help="ms released before the next (measured: 0.04%% left at 100 ms)")
    ap.add_argument("--palette", default=None)
    ap.add_argument("--light", choices=["pair", "weighted"], default="pair",
                    help="pair: today's two-pixel hand/finger map; "
                         "weighted: the continuous model in sim/lightfield.py")
    ap.add_argument("--diffuser", action="store_true",
                    help="show the diffuser surface lit by the summed light field")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--frames", default=None)
    ap.add_argument("--camera", default="bar_closeup")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--dip-target", type=float, default=10.0)
    ap.add_argument("--dip-min", type=float, default=8.5)
    ap.add_argument("--dip-max", type=float, default=10.5)
    ap.add_argument("--rest-tol", type=float, default=0.5, help="mm still down after release")
    ap.add_argument("--max-offset", type=float, default=7.0,
                    help="mm between a lit LED and the key it belongs to, per note")
    ap.add_argument("--max-mean-offset", type=float, default=3.0,
                    help="mm, averaged over the sweep -- catches a misaligned tape")
    args = ap.parse_args()

    rig = Rig(args.scene)
    notes = notes_from(args)
    if args.selftest:
        return run_selftest(rig, notes, args)
    if args.frames:
        return run_frames(rig, notes, args)
    return run_live(rig, notes, args)


if __name__ == "__main__":
    sys.exit(main())
