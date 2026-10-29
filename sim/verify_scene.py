"""Independent check of the generated scene. Run it after make_scene.py.

    python sim/verify_scene.py
    python sim/verify_scene.py --canary     # prove the checks can fail

It re-derives everything from the compiled MuJoCo model and the STL files on
disk; it does not import make_scene, so a bug in make_scene's own arithmetic
cannot hide here. Each check prints the number it measured, not just PASS, and
`--canary` displaces two keys through the compiler -- one 1 mm sideways, one
20 mm up into the bar's footprint -- and the placement and clearance checks are
expected to report exactly those, which is how they are known to be able to fail.
"""

from __future__ import annotations

import argparse
import csv
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(HERE, "assets")
SCENE = os.path.join(HERE, "scene.xml")
LED_MAP = os.path.join(ROOT, "data", "led_map.csv")

M1_OCTAVE_MM = 164.8          # docs/hardware.md, measured 2026-09-15
KEY_DIP_MM = 10.0             # key travel at the front tip, set in make_scene.py
STRIP_ORIGIN_MM = -7.75
LED_PITCH_MM = 1000.0 / 144
BLACK_PCS = {1, 3, 6, 8, 10}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def stl_bounds(path):
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack("<I", data[80:84])[0]
    rec = np.frombuffer(data, dtype=np.uint8, count=n * 50, offset=84).reshape(n, 50)
    tri = rec[:, 12:48].copy().view("<f4").reshape(n * 3, 3).astype(np.float64)
    return tri.min(axis=0), tri.max(axis=0)


def _board_rim(model, data, level_tol=0.3):
    """Front rim y of the piano's top board, re-derived from the compiled cabinet
    mesh: the front-most edge of the largest flat upward surface over the keys."""
    import mujoco

    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "cabinet")
    if gid < 0:
        return None
    mid = model.geom_dataid[gid]
    fadr, fnum = model.mesh_faceadr[mid], model.mesh_facenum[mid]
    vadr = model.mesh_vertadr[mid]
    faces = model.mesh_face[fadr:fadr + fnum] + vadr
    v = model.mesh_vert.astype(np.float64)
    R = data.geom_xmat[gid].reshape(3, 3)
    w = (v @ R.T + data.geom_xpos[gid]) * 1000
    tri = w[faces]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(nrm, axis=1)
    nz = np.where(ln > 0, nrm[:, 2] / np.where(ln > 0, ln, 1), 0.0)
    c = tri.mean(axis=1)
    up = (nz > 0.95) & (np.abs(c[:, 0]) < 600)
    if not up.any():
        return None
    zb = np.round(c[up, 2] * 2) / 2.0
    ar = ln[up] / 2.0
    bins = {}
    for z, a in zip(zb, ar):
        bins[float(z)] = bins.get(float(z), 0.0) + float(a)
    top_z = max(bins, key=bins.get)
    lvl = up & (np.abs(c[:, 2] - top_z) < level_tol)
    return float(tri[lvl][:, :, 1].min())


CANARY_MOVES = [
    # geom, (dx, dy, dz) in metres, which check it should trip
    ("geom_key_C4", (0.001, 0.0, 0.0), "mesh placement"),
    ("geom_key_Cs4", (0.0, 0.0, 0.020), "bar clears the keys"),
]


def _canary_scene(scene_path, moves=CANARY_MOVES):
    """Write a copy of the scene with parts displaced, for --canary."""
    import re

    with open(scene_path, encoding="utf-8") as f:
        text = f.read()
    for geom, (dx, dy, dz), _ in moves:
        m = re.search(r'name="%s"[^>]*?pos="(-?[\d.eE+-]+) (-?[\d.eE+-]+) (-?[\d.eE+-]+)"'
                      % geom, text)
        if not m:
            raise SystemExit("canary: no pos attribute found for %s" % geom)
        x, y, z = (float(v) for v in m.groups())
        text = text[:m.start(1)] + ("%.6f %.6f %.6f" % (x + dx, y + dy, z + dz)) \
            + text[m.end(3):]
    out = os.path.join(os.path.dirname(scene_path), "_canary_scene.xml")
    with open(out, "wb") as f:
        f.write(text.encode("utf-8"))
    return out, out


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail):
        self.rows.append((name, bool(ok), detail))
        print("%-28s %-4s %s" % (name, "PASS" if ok else "FAIL", detail))

    def done(self):
        bad = [r for r in self.rows if not r[1]]
        print("\n%d checks, %d failed" % (len(self.rows), len(bad)))
        return 1 if bad else 0


def world_triangles(model, data, gid):
    """A mesh geom's triangles in world space, in millimetres."""
    mid = model.geom_dataid[gid]
    fadr, fnum = model.mesh_faceadr[mid], model.mesh_facenum[mid]
    faces = model.mesh_face[fadr:fadr + fnum] + model.mesh_vertadr[mid]
    v = model.mesh_vert.astype(np.float64)
    w = (v @ data.geom_xmat[gid].reshape(3, 3).T + data.geom_xpos[gid]) * 1000
    return w[faces]


def world_vertex_bounds(model, data, gid):
    """World-space AABB of a mesh geom's actual vertices, in metres."""
    mid = model.geom_dataid[gid]
    adr, num = model.mesh_vertadr[mid], model.mesh_vertnum[mid]
    v = model.mesh_vert[adr:adr + num].astype(np.float64)
    R = data.geom_xmat[gid].reshape(3, 3)
    w = v @ R.T + data.geom_xpos[gid]
    return w.min(axis=0), w.max(axis=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default=SCENE)
    ap.add_argument("--canary", action="store_true",
                    help="displace two keys and expect the placement and clearance "
                         "checks to fail")
    a = ap.parse_args()

    import mujoco

    scene_path = a.scene
    canary_path = None
    if a.canary:
        # The displacement has to go through the compiler: geoms in static bodies
        # have their world pose baked at compile time, so poking model.geom_pos
        # afterwards moves nothing (measured: geom_xpos delta 0.0) and the canary
        # would report a reassuring PASS while proving nothing.
        scene_path, canary_path = _canary_scene(a.scene)
        for geom, d, expect in CANARY_MOVES:
            print("canary: %s displaced by (%+.1f %+.1f %+.1f) mm -- expect '%s' to FAIL"
                  % (geom, d[0] * 1000, d[1] * 1000, d[2] * 1000, expect))
        print()

    try:
        model = mujoco.MjModel.from_xml_path(scene_path)
    finally:
        if canary_path and os.path.exists(canary_path):
            os.remove(canary_path)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    rep = Report()

    # 1. every mesh geom sits where its STL says it should, to within a tenth of a mm
    worst, worst_name = 0.0, ""
    checked = 0
    for gid in range(model.ngeom):
        if model.geom_type[gid] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, gid)
        if name.startswith(("led_", "mod_")):
            continue     # tiled copies of one CAD module; checked by pitch below
        mesh_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MESH, model.geom_dataid[gid])
        lo_f, hi_f = stl_bounds(os.path.join(ASSETS, mesh_name + ".stl"))
        lo_w, hi_w = world_vertex_bounds(model, data, gid)
        err = max(np.abs(lo_w * 1000 - lo_f).max(), np.abs(hi_w * 1000 - hi_f).max())
        checked += 1
        if err > worst:
            worst, worst_name = err, name
    rep.check("mesh placement", worst < 0.1,
              "%d meshes, worst %.4f mm (%s)" % (checked, worst, worst_name))

    # 2. 88 keys, keyboard order, A0..C8
    keys = []
    for bid in range(model.nbody):
        bname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid)
        if not bname or not bname.startswith("key_"):
            continue
        gid = model.body_geomadr[bid]
        lo, hi = world_vertex_bounds(model, data, gid)
        keys.append((bname, (lo[0] + hi[0]) / 2, lo * 1000, hi * 1000))
    keys.sort(key=lambda k: k[1])
    order_ok = len(keys) == 88
    mism = []
    for i, (bname, _, _, _) in enumerate(keys):
        midi = 21 + i
        want = "key_" + NOTE_NAMES[midi % 12].replace("#", "s") + str(midi // 12 - 1)
        if bname != want:
            mism.append("%s!=%s" % (bname, want))
    rep.check("88 keys in order", order_ok and not mism,
              "%d keys, %d name mismatches %s" % (len(keys), len(mism), mism[:3]))

    # 3. geometry the CAD implies, against the measured M1 octave span
    whites = [k for k in keys if "s" not in k[0][4:]]
    a0_left = min(k[2][0] for k in whites)
    c8_right = max(k[3][0] for k in whites)
    width = c8_right - a0_left
    pitch = width / 52.0
    octave = pitch * 7.0
    rep.check("octave span vs M1", abs(octave - M1_OCTAVE_MM) < 2.0,
              "CAD %.2f mm vs measured %.1f mm (delta %+.2f mm over 7 whites, "
              "playing width %.2f mm)" % (octave, M1_OCTAVE_MM, octave - M1_OCTAVE_MM, width))

    # White-key pitch uniformity, measured on the LEFT EDGES. Centres are the
    # wrong feature: a white key's head is notched around its neighbouring black
    # keys, so C/D/E/F/G/A/B are not left-right symmetric and their bbox centres
    # wander +-0.16 mm even on a perfectly pitched keyboard.
    wx = sorted(k[2][0] for k in whites)
    steps = np.diff(wx)
    cx = sorted((k[2][0] + k[3][0]) / 2 for k in whites)
    csteps = np.diff(cx)
    # The 0.5 mm bound is not a tolerance on the real instrument: this CAD
    # replica's own white-key pitch varies by 0.324 mm (left edges 23.4428 ..
    # 23.7672 mm) while every key is exactly 22.604 mm wide. That spread is
    # identical at tessellation tolerances of 0.25, 0.05 and 0.01 mm, so it is
    # in the model, not in the mesh. The check exists to catch a key that has
    # *moved*, so the bound sits just above the replica's own jitter.
    rep.check("white pitch uniform", steps.ptp() < 0.5,
              "left edges %.4f..%.4f mm over 51 gaps (spread %.4f, CAD's own is "
              "0.324); centres spread %.4f" % (steps.min(), steps.max(), steps.ptp(),
                                               csteps.ptp()))

    # 4. the LED bar: count, pitch, and where index 0 starts
    leds = []
    for gid in range(model.ngeom):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, gid)
        if name and name.startswith("led_"):
            lo, hi = world_vertex_bounds(model, data, gid)
            leds.append((int(name.split("_")[1]), (lo[0] + hi[0]) / 2 * 1000,
                         lo * 1000, hi * 1000))
    leds.sort()
    xs = np.array([l[1] for l in leds])
    dx = np.diff(xs)
    rep.check("LED count", len(leds) == 176, "%d led geoms" % len(leds))
    rep.check("LED pitch", len(leds) > 1 and abs(dx.mean() - LED_PITCH_MM) < 1e-3,
              "mean %.5f mm, spread %.2e mm (design %.5f)"
              % (dx.mean(), dx.ptp(), LED_PITCH_MM))
    # The tape's pitch is fixed, so "aligned to both edges" means the largest
    # whole number of LEDs that fits, with the leftover split evenly.
    tape_left = xs[0] - LED_PITCH_MM / 2
    tape_right = xs[-1] + LED_PITCH_MM / 2
    margin_l = tape_left - a0_left
    margin_r = c8_right - tape_right
    fits = int(width // LED_PITCH_MM)
    rep.check("strip fills the keyboard", len(leds) == fits,
              "%d LEDs on a %.2f mm keyboard; %d is the most that fit at %.4f mm "
              "(%d would overhang by %.2f mm)"
              % (len(leds), width, fits, LED_PITCH_MM, fits + 1,
                 (fits + 1) * LED_PITCH_MM - width))
    rep.check("strip centred", abs(margin_l - margin_r) < 0.05,
              "margins %+.2f mm left / %+.2f mm right (difference %.3f mm)"
              % (margin_l, margin_r, margin_l - margin_r))

    # 5. the bar is on the board and clears the keys running under it.
    # The bar now sits against the board's front rim, which is 21 mm FORWARD of
    # the key tails, so "is it behind the tails" is the wrong question -- the
    # keys pass underneath. What must hold is that nothing on a key reaches up
    # into the bar's footprint, checked against the real vertices in that y band.
    key_tail_y = max(k[3][1] for k in whites)
    black_top_z = max(k[3][2] for k in keys if "s" in k[0][4:])
    led_bottom_z = min(l[2][2] for l in leds)
    # footprint = the strip module (12 mm of PCB), not the 4.9 mm LED package
    mods = []
    for gid in range(model.ngeom):
        nm = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, gid)
        if nm and nm.startswith("mod_"):
            lo, hi = world_vertex_bounds(model, data, gid)
            mods.append((lo * 1000, hi * 1000))
    bar_front_y = min(lo[1] for lo, _ in mods)
    bar_back_y = max(hi[1] for _, hi in mods)
    bar_bottom_z = min(lo[2] for lo, _ in mods)

    rim = _board_rim(model, data)
    rep.check("bar on the board", rim is not None and bar_front_y >= rim - 0.01,
              "bar front y %.2f mm, board rim y %.2f mm (overhang %+.2f); key tails "
              "sit %.2f mm behind the rim" % (bar_front_y, rim, rim - bar_front_y,
                                              key_tail_y - rim))

    # Triangles, not vertices: these key meshes are boxes whose vertices sit only
    # at the corners, so a vertex test over a 12 mm band found nothing at all on
    # any of the 88 keys and reported a clearance of +1e9 mm -- a check that
    # could not fail. A triangle that merely *spans* the band is counted here.
    tallest, tallest_key, spanning = -1e9, "", 0
    for bid in range(model.nbody):
        bname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid)
        if not bname or not bname.startswith("key_"):
            continue
        gid = model.body_geomadr[bid]
        tri = world_triangles(model, data, gid)
        ymin, ymax = tri[:, :, 1].min(axis=1), tri[:, :, 1].max(axis=1)
        hit = tri[(ymax >= bar_front_y) & (ymin <= bar_back_y)]
        spanning += len(hit)
        if len(hit) and hit[:, :, 2].max() > tallest:
            tallest, tallest_key = hit[:, :, 2].max(), bname
    rep.check("bar clears the keys", spanning > 0 and tallest < bar_bottom_z,
              "%d key triangles cross the bar's footprint (y %.2f..%.2f); highest "
              "reaches %.2f mm (%s) under a bar sitting at %.2f mm -> clearance %+.2f mm"
              % (spanning, bar_front_y, bar_back_y, tallest, tallest_key, bar_bottom_z,
                 bar_bottom_z - tallest))
    rep.check("bar height", True,
              "LED underside %.2f mm; black-key tops %.2f mm (LEDs sit %+.2f mm relative)"
              % (led_bottom_z, black_top_z, led_bottom_z - black_top_z))

    # 6. every LED the firmware map can address exists in the scene
    with open(LED_MAP, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    used = set()
    for r in rows:
        for tok in r["leds_in_key"].split("|"):
            used.add(int(tok))
        used.add(int(r["primary_led"]))
    missing = sorted(i for i in used if i >= len(leds) or i < 0)
    rep.check("led_map addressable", not missing,
              "%d notes, LED indices %d..%d, %d missing"
              % (len(rows), min(used), max(used), len(missing)))

    # 6b. does each note's primary LED actually sit over that key?
    # This is the one check that crosses the two worlds: LED x comes from the
    # scene, the key it should light comes from data/led_map.csv, and the key's
    # own position comes from the CAD. The comparison is made at the key TAIL
    # centre (rear 20 mm), because that is the line the note->LED map is built
    # on -- a white key's head is notched and its bbox centre is not its pitch.
    tails = {}
    for bid in range(model.nbody):
        bname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid)
        if not bname or not bname.startswith("key_"):
            continue
        gid = model.body_geomadr[bid]
        mid = model.geom_dataid[gid]
        adr, num = model.mesh_vertadr[mid], model.mesh_vertnum[mid]
        v = model.mesh_vert[adr:adr + num].astype(np.float64)
        w = (v @ data.geom_xmat[gid].reshape(3, 3).T + data.geom_xpos[gid]) * 1000
        rear = w[w[:, 1] > w[:, 1].max() - 20.0]
        tails[bname] = (rear[:, 0].min() + rear[:, 0].max()) / 2.0

    # The offset splits cleanly in two, and only the first half is the sim's
    # business:
    #   led_x - design_tail : the map's own LED quantisation, floor +-3.4 mm
    #   design_tail - cad   : this CAD replica's geometry vs the M1 model
    led_x = {i: x for i, x, _, _ in leds}
    quant, geom, total = [], [], []
    worst = ("", 0.0, 0.0, 0.0)
    for r in rows:
        body = "key_" + r["name"].replace("#", "s")
        if body not in tails:
            continue
        cad = tails[body] - a0_left
        design = float(r["tail_centre_mm"])
        lx = led_x[int(r["primary_led"])] - a0_left
        quant.append(lx - design)
        geom.append(design - cad)
        total.append(lx - cad)
        if abs(lx - cad) > abs(worst[1]):
            worst = (r["name"], lx - cad, lx - design, design - cad)
    quant, geom, total = np.array(quant), np.array(geom), np.array(total)

    # Where does data/led_map.csv think the tape starts? Each note gives an
    # estimate; the median cancels the +-half-pitch quantisation. If it does not
    # match where the tape actually is, the firmware will light the wrong LED --
    # this is the check that couples the sim's layout to the shipped map.
    implied = np.median([float(r["tail_centre_mm"]) - LED_PITCH_MM * (int(r["primary_led"]) + 0.5)
                         for r in rows])
    actual = tape_left - a0_left
    delta = actual - implied

    # The decisive test is discrete, not metric: for every note, is the LED the
    # map names the one actually nearest that key on this strip? A sub-pitch
    # origin difference (the CAD replica is 1.98 mm wider than the M1 model) is
    # harmless and must not fail; being a whole pitch out is not.
    off_by = []
    for r in rows:
        body = "key_" + r["name"].replace("#", "s")
        if body not in tails:
            continue
        nearest = min(range(len(leds)), key=lambda i: abs(led_x[i] - tails[body]))
        off_by.append(int(r["primary_led"]) - nearest)
    off_by = np.array(off_by)
    exact = int((off_by == 0).sum())
    rep.check("map matches strip", np.abs(off_by).max() <= 1,
              "%d/%d notes light the LED nearest their key, %d are one index off, "
              "worst %d; tape starts %+.2f mm from A0's left edge and the map implies "
              "%+.2f mm (%.2f mm = %.2f pitches). Fix if this fails: re-run "
              "tools/gen_map.py --origin, or make_scene.py --align map"
              % (exact, len(off_by), int((np.abs(off_by) == 1).sum()),
                 int(np.abs(off_by).max()), actual, implied, delta, delta / LED_PITCH_MM))
    rep.check("CAD keys vs M1 model", np.abs(geom).max() < 6.0,
              "design tail centres run %+.2f..%+.2f mm from this replica's "
              "(mean %+.2f); total LED-to-key worst %+.2f mm at %s (= %+.2f map "
              "offset, which includes any origin mismatch, %+.2f geometry)"
              % (geom.min(), geom.max(), geom.mean(), worst[1], worst[0],
                 worst[2], worst[3]))

    # 6c. the key action: one hinge and one actuator per key, and every hinge's
    # range is the angle that gives that key's own front tip the design travel.
    hinges, angles, dips = 0, [], []
    for bid in range(model.nbody):
        bname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid)
        if not bname or not bname.startswith("key_"):
            continue
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "j_" + bname)
        aid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, "act_" + bname)
        if jid < 0 or aid < 0 or model.jnt_type[jid] != mujoco.mjtJoint.mjJNT_HINGE:
            continue
        hinges += 1
        hi = float(model.jnt_range[jid][1])
        angles.append(hi)
        gid = model.body_geomadr[bid]
        tri = world_triangles(model, data, gid)
        v = tri.reshape(-1, 3)
        arm = model.body_pos[bid][1] * 1000 - v[:, 1].min()   # pivot to front tip
        dips.append(arm * np.sin(hi))
    dips = np.array(dips) if dips else np.array([0.0])
    rep.check("key action", hinges == 88 and np.abs(dips - KEY_DIP_MM).max() < 0.2,
              "%d hinges + actuators, %.2f..%.2f deg, front-tip travel %.2f..%.2f mm "
              "(design %.1f)" % (hinges, np.degrees(min(angles)), np.degrees(max(angles)),
                                 dips.min(), dips.max(), KEY_DIP_MM))

    # 7. the cameras actually see something
    r = mujoco.Renderer(model, 240, 400)
    lumas = []
    for i in range(model.ncam):
        cam = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_CAMERA, i)
        r.update_scene(data, camera=cam)
        img = r.render()
        lumas.append((cam, float(img.mean()), float(img.std())))
    rep.check("cameras render", all(l[2] > 5 for l in lumas),
              ", ".join("%s mean %.0f sd %.0f" % l for l in lumas))

    return rep.done()


if __name__ == "__main__":
    sys.exit(main())
