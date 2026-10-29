"""Generate `sim/scene.xml` (MuJoCo) from the meshes built by build_assets.py.

    python sim/build_assets.py --part piano
    python sim/build_assets.py --part strip
    python sim/make_scene.py

The scene is GENERATED. Edit this script, never scene.xml -- a re-run overwrites
it. Every position in it is derived here from the CAD meshes plus the four design
constants below; nothing is placed by eye in a 3D viewer.

What it builds
--------------
* `cabinet`: the piano minus its keys, one mesh;
* one body per key, named after the MIDI note it sounds (`key_A0` .. `key_C8`),
  so a later revision can drop a hinge joint into each one without renaming
  anything. The note each mesh gets is decided by x order and then checked
  against the real black/white pattern -- see `key_table`;
* the LED bar: the CAD's own 7 mm WS2812 module tiled once per LED, with
  the LED package as its own geom `led_000` .. `led_175`. Index n is index n of
  `data/led_map.csv`, so lighting the LED for a note needs no arithmetic in
  between.

Placement
---------
MuJoCo re-centres mesh vertices when it loads them, so the two-pass step in
`main` is not optional: pass 1 emits every mesh geom at the origin, compiles, and
reads back each geom's local AABB; pass 2 re-emits with
`pos = target_centre - local_aabb_centre`, which lands every part exactly where
the CAD put it whatever MuJoCo did internally. `verify_scene.py` re-checks that
from the compiled model, independently of this file.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(HERE, "assets")

# --- design constants, from docs/hardware.md (the M1 = 164.8 mm/octave run) ---
STRIP_ORIGIN_MM = -7.75      # strip start relative to A0's left edge, as gen_map chose it
LED_PITCH_MM = 1000.0 / 144  # 144 LED/m -> 6.9444 mm, fixed by the tape
DESIGN_LED_COUNT = 176       # design bar: 176 LEDs, 1222.21 mm

# How the strip is positioned along the keyboard:
#   "centre" - as many whole LEDs as fit between A0's left edge and C8's right
#              edge, with the leftover split equally at the two ends;
#   "map"    - at gen_map's original min-error origin, STRIP_ORIGIN_MM, kept so
#              the old placement can be reproduced for comparison.
# The shipped map was regenerated for a centred tape on 2026-09-21
# (tools/gen_map.py 164.8 --origin centre); a run prints where the tape sits
# against what data/led_map.json actually says, read from the file.
STRIP_ALIGN = "centre"
# The bar sits against the front rim of the top board -- the lip the keys
# disappear under -- not at an arbitrary setback from the key tails. On this
# instrument the board overhangs the key tails by ~21 mm, so anchoring on the
# tails parked the bar in the middle of the board, 21 mm further from the keys
# than it needs to be. Raise this to pull the bar back from the rim.
BAR_FROM_RIM_MM = 0.0

# --- diffuser -----------------------------------------------------------------
# A silicone/PC profile over the tape (BOM: T0515, 12 mm). docs/hardware.md wants
# a 6-10 mm LED-to-diffuser gap: under ~4 mm the dots still show, over ~12 mm a
# key bleeds into its neighbour. The surface is drawn as many thin segments whose
# colour is the *summed* light field, because MuJoCo has no light transport -- a
# plain translucent lid would show dots under plastic and teach us nothing.
sys.path.insert(0, HERE)
import lightfield as _lf                      # noqa: E402  (single source for the gap)

DIFFUSER_GAP_MM = _lf.DIFFUSER_GAP_MM         # set by continuity, see lightfield.py
DIFFUSER_THICK_MM = 1.5
DIFFUSER_SEGMENTS_PER_LED = 3  # 2.31 mm per segment at a 6.9444 mm pitch
DIFFUSER_OVERHANG_MM = 0.6     # cap wider than the tape, so no seam shows
WALL_THICK_MM = 0.8            # the profile's side walls: what hides the emitters

# --- key action -------------------------------------------------------------
# A real key dips ~10 mm at its front tip and pivots on a balance rail near its
# rear. Each key therefore gets its own hinge angle: the pivot-to-tip distance
# differs between white (~168 mm) and black (~117 mm) keys, and a shared angle
# would give them different touch depths.
KEY_DIP_MM = 10.0        # travel at the front tip, the same for every key
PIVOT_INSET_MM = 8.0     # pivot this far in front of the key's own rear end
KEY_MASS_KG = {"white": 0.030, "black": 0.020}
KEY_KP = 5.0             # position-actuator gain; measured step response in
KEY_KV = 0.08            # sim/keysweep.py --steptest, not guessed
KEY_DAMPING = 0.01

MIDI_LOW, MIDI_HIGH = 21, 108
BLACK_PCS = {1, 3, 6, 8, 10}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
S = 0.001  # mm -> m


def note_name(midi):
    return "%s%d" % (NOTE_NAMES[midi % 12], midi // 12 - 1)


def stl_tris(path):
    """Triangles of a binary STL as (n, 3, 3), in file units (mm)."""
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack("<I", data[80:84])[0]
    rec = np.frombuffer(data, dtype=np.uint8, count=n * 50, offset=84).reshape(n, 50)
    return rec[:, 12:48].copy().view("<f4").reshape(n, 3, 3).astype(np.float64)


def stl_bounds(path):
    """Vertex AABB of a binary STL -- exactly the vertices MuJoCo will load."""
    t = stl_tris(path).reshape(-1, 3)
    return t.min(axis=0), t.max(axis=0)


def asset(name):
    return os.path.join(ASSETS, name + ".stl")


def map_origin_mm():
    """Where data/led_map.json says the tape starts, relative to A0's left edge.

    Read, never assumed: the map has been regenerated since STRIP_ORIGIN_MM was
    written, and printing the constant instead of the file said the tape was
    9.75 mm out when it was 1.0 mm out.
    """
    with open(os.path.join(ROOT, "data", "led_map.json"), "rb") as f:
        return float(json.load(f)["strip_origin_mm"])


def load_part(part):
    with open(os.path.join(ASSETS, "%s_manifest.json" % part), "rb") as f:
        return json.load(f)


def find_shelf(tail_y, x_lo, x_hi, depth=30.0, min_area=1000.0):
    """The flat surface behind the key tails that the bar would be taped to.

    Upward-facing cabinet triangles in the band the bar actually occupies are
    binned by height and weighted by area; the bin with the most area wins.
    Returns (z, area, candidates) so the caller can print what it chose -- a
    height read off a CAD viewer by eye would not survive a CAD update.

    The band is deliberately shallow. Over the full 90 mm behind the tails this
    piano offers four flat levels (95, 97, 98, 100 mm) plus a 103.5 mm ridge, and
    picking "the highest one with real area" put the bar on the ridge.
    """
    tri = stl_tris(asset("cabinet"))
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(nrm, axis=1)
    area = length / 2.0
    with np.errstate(invalid="ignore", divide="ignore"):
        nz = np.where(length > 0, nrm[:, 2] / np.where(length > 0, length, 1), 0.0)
    c = tri.mean(axis=1)
    m = ((nz > 0.95) & (c[:, 1] > tail_y) & (c[:, 1] < tail_y + depth)
         & (c[:, 0] > x_lo) & (c[:, 0] < x_hi))
    if not m.any():
        return None, 0.0, [], None
    zb = np.round(c[m, 2] * 2) / 2.0
    bins = {}
    for z, a in zip(zb, area[m]):
        bins[float(z)] = bins.get(float(z), 0.0) + float(a)
    cands = sorted(bins.items(), key=lambda kv: -kv[1])[:5]
    z, area = cands[0]
    if area < min_area:
        raise SystemExit("no flat surface of at least %.0f mm2 found %.0f..%.0f mm behind "
                         "the key tails; candidates %s" % (min_area, tail_y, tail_y + depth,
                                                           cands))
    # front rim of that surface: the lip the keys run under, and the forward
    # limit for the bar. Taken over the whole board, not just the search band.
    lvl = (nz > 0.95) & (np.abs(tri[:, :, 2].mean(axis=1) - z) < 0.3) \
        & (c[:, 0] > x_lo) & (c[:, 0] < x_hi)
    rim = float(tri[lvl][:, :, 1].min())
    return z, area, cands, rim


def key_table(piano):
    """Every key mesh, ordered left to right, with the MIDI note it sounds.

    build_assets numbers white and black key shells separately; sorting all 88 by
    centre x interleaves them back into keyboard order. The black/white pattern
    that falls out is asserted against a real keyboard, which is what proves both
    the ordering and the A0 start instead of assuming them.
    """
    keys = []
    for name in piano["groups"]:
        if name.startswith(("white_key_", "black_key_")):
            lo, hi = stl_bounds(asset(name))
            keys.append({"mesh": name, "black": name.startswith("black"),
                         "min": lo, "max": hi, "centre": (lo + hi) / 2.0})
    keys.sort(key=lambda k: k["centre"][0])
    if len(keys) != MIDI_HIGH - MIDI_LOW + 1:
        raise SystemExit("expected 88 key meshes, found %d" % len(keys))
    for i, k in enumerate(keys):
        midi = MIDI_LOW + i
        k["midi"], k["note"] = midi, note_name(midi)
        k["body"] = "key_" + k["note"].replace("#", "s")
        k["joint"] = "j_" + k["body"]
        k["actuator"] = "act_" + k["body"]
        if ((midi % 12) in BLACK_PCS) != k["black"]:
            raise SystemExit("key order is not a piano: index %d (%s) is %s in the CAD"
                             % (i, k["note"], "black" if k["black"] else "white"))
        # Hinge on the balance rail: axis along +x, a little in front of the rear
        # end, at the key's own mid-height. A positive rotation about +x lowers
        # the front (a point at -y moves to z -= y*sin(theta)).
        k["pivot"] = np.array([k["centre"][0],
                               k["max"][1] - PIVOT_INSET_MM,
                               (k["min"][2] + k["max"][2]) / 2.0])
        k["arm_mm"] = float(k["pivot"][1] - k["min"][1])
        k["angle"] = float(np.arctan2(KEY_DIP_MM, k["arm_mm"]))
        k["mass"] = KEY_MASS_KG["black" if k["black"] else "white"]
    return keys


def strip_alignment(align, playing_width, a0_left):
    """Where the tape starts and how many LEDs it carries.

    Returns (origin_x, count, margin_left, margin_right). The pitch is fixed by
    the tape, so "aligned to both edges" means the largest whole number of LEDs
    that fits, with the remainder split evenly -- not a stretched pitch.
    """
    fits = int(playing_width // LED_PITCH_MM)
    if align == "centre":
        count = fits
        margin = (playing_width - count * LED_PITCH_MM) / 2.0
        return a0_left + margin, count, margin, margin
    if align == "map":
        count = DESIGN_LED_COUNT
        left = STRIP_ORIGIN_MM
        return a0_left + left, count, left, playing_width - left - count * LED_PITCH_MM
    raise SystemExit("unknown STRIP_ALIGN %r (want 'centre' or 'map')" % align)


def compute_layout(piano, strip, keys, align=STRIP_ALIGN):
    whites = [k for k in keys if not k["black"]]
    blacks = [k for k in keys if k["black"]]
    a0_left = min(k["min"][0] for k in whites)
    c8_right = max(k["max"][0] for k in whites)
    playing_width = c8_right - a0_left
    white_pitch = playing_width / 52.0
    tail_y = max(k["max"][1] for k in whites)
    front_y = min(k["min"][1] for k in whites)
    white_top_z = max(k["max"][2] for k in whites)
    black_top_z = max(k["max"][2] for k in blacks)

    shelf_z, shelf_area, shelf_cands, shelf_rim_y = find_shelf(tail_y, a0_left, c8_right)

    # --- the CAD module: LED package and the rest of one 7 mm tile ---
    chip_lo, chip_hi = stl_bounds(asset("led_chip"))
    mod_lo, mod_hi = stl_bounds(asset("strip_module"))
    chip_c = (chip_lo + chip_hi) / 2.0
    mod_c = (mod_lo + mod_hi) / 2.0
    strip_width = mod_hi[1] - mod_lo[1]
    strip_thick = mod_hi[2] - mod_lo[2]
    chip_top = chip_hi[2]

    # --- bar placement: flat on the board, against its front rim ---
    y_front = (shelf_rim_y if shelf_rim_y is not None else tail_y) + BAR_FROM_RIM_MM
    y_centre = y_front + strip_width / 2.0
    z_base = shelf_z if shelf_z is not None else white_top_z

    origin_x, led_count, margin_l, margin_r = strip_alignment(align, playing_width, a0_left)

    # Tile i is translated so its LED package centre lands exactly on the target
    # position; the module geometry follows the same translation.
    tiles = []
    for i in range(led_count):
        x_led = origin_x + (i + 0.5) * LED_PITCH_MM
        t = np.array([x_led - chip_c[0], y_centre - mod_c[1], z_base - mod_lo[2]])
        tiles.append({"i": i, "t": t,
                      "led_centre": chip_c + t, "mod_centre": mod_c + t})

    bar_top_z = chip_top + tiles[0]["t"][2]

    n_seg = led_count * DIFFUSER_SEGMENTS_PER_LED
    seg_len = LED_PITCH_MM / DIFFUSER_SEGMENTS_PER_LED
    diff_z = bar_top_z + DIFFUSER_GAP_MM + DIFFUSER_THICK_MM / 2.0
    diffuser = {
        "segments": n_seg, "seg_len_mm": seg_len,
        "cap_width_mm": strip_width + 2 * DIFFUSER_OVERHANG_MM,
        "wall_thick_mm": WALL_THICK_MM,
        "wall_z_lo_mm": z_base, "wall_z_hi_mm": bar_top_z + DIFFUSER_GAP_MM,
        "z_centre_mm": diff_z, "thickness_mm": DIFFUSER_THICK_MM,
        "gap_mm": DIFFUSER_GAP_MM, "width_mm": strip_width,
        "x0_mm": origin_x,
        "centres_mm": [origin_x + (i + 0.5) * seg_len for i in range(n_seg)],
    }
    centre = [(a0_left + c8_right) / 2.0, (front_y + tail_y) / 2.0, white_top_z]
    cams = {
        "player": ([centre[0], front_y - 620.0, white_top_z + 430.0], "cabinet"),
        "overhead": ([centre[0], centre[1] - 40.0, white_top_z + 1000.0], "cabinet"),
        "bar_closeup": ([centre[0] - 55.0, front_y - 195.0, white_top_z + 150.0], "led_bar"),
        "left_end": ([a0_left - 240.0, front_y - 300.0, white_top_z + 250.0], "led_bar"),
        # close on the bar around middle C, for judging the light itself
        "bar_macro": ([centre[0] - 60.0, tail_y - 380.0, bar_top_z + 210.0], "diffuser"),
    }

    return {
        "a0_left_x": a0_left, "c8_right_x": c8_right,
        "playing_width_mm": playing_width, "white_pitch_mm": white_pitch,
        "octave_span_mm": white_pitch * 7.0,
        "key_tail_y": tail_y, "key_front_y": front_y,
        "white_top_z": white_top_z, "black_top_z": black_top_z,
        "shelf_z": shelf_z, "shelf_area_mm2": shelf_area, "shelf_candidates": shelf_cands,
        "shelf_rim_y": shelf_rim_y,
        "strip_width_mm": strip_width, "strip_thickness_mm": strip_thick,
        "chip_size_mm": (chip_hi - chip_lo).tolist(),
        "bar_y_front_mm": y_front, "bar_y_centre_mm": y_centre, "bar_base_z_mm": z_base,
        "bar_top_z_mm": bar_top_z,
        "bar_above_white_mm": bar_top_z - white_top_z,
        "bar_above_black_mm": bar_top_z - black_top_z,
        "bar_len_mm": LED_PITCH_MM * led_count,
        "led_count": led_count, "strip_align": align,
        "strip_origin_x": origin_x,
        "strip_origin_from_a0_mm": origin_x - a0_left,
        "margin_left_mm": margin_l, "margin_right_mm": margin_r,
        "leds_that_fit": int(playing_width // LED_PITCH_MM),
        "cad_strip_len_mm": (strip["cad_strip_bbox"]["max"][0]
                             - strip["cad_strip_bbox"]["min"][0]),
        "cad_led_count": strip["cad_led_count"],
        "cad_module_pitch_mm": strip["cad_module_pitch_mm"],
        "tiles": tiles, "scene_centre": centre, "cameras": cams,
        "diffuser": diffuser,
        "diffuser_above_black_mm": diff_z + DIFFUSER_THICK_MM / 2.0 - black_top_z,
    }


def mat(name, rgba, **kw):
    extra = "".join(' %s="%s"' % (k, v) for k, v in kw.items())
    return '    <material name="%s" rgba="%s"%s/>' % (name, rgba, extra)


def build_xml(piano, keys, layout, off=None):
    off = off or {}
    L = []
    A = L.append

    def geom(name, mesh, material, extra=""):
        A('      <geom name="%s" type="mesh" mesh="%s" material="%s" pos="%s"'
          ' contype="0" conaffinity="0" group="1"%s/>'
          % (name, mesh, material, off.get(name, "0 0 0"), extra))

    A('<!-- GENERATED by sim/make_scene.py - do not edit, re-run the script. -->')
    A('<mujoco model="finger_light">')
    A('  <compiler meshdir="assets" angle="radian" autolimits="true"/>')
    # Gravity off, deliberately. Each key hinges about a rail with its centre of
    # mass in front of the pivot, so weight torques it straight into its own
    # travel: with gravity on, every key sagged 0.023 rad -- 4.3 mm at the tip --
    # and never returned to rest. A real key is held up by its return spring
    # against a front-rail stop; nothing else in this scene has contacts or
    # dynamics worth simulating, so the cheap, honest model is a weightless key
    # whose position actuator alone decides where it sits.
    A('  <option gravity="0 0 0" timestep="0.002" integrator="implicitfast"/>')
    A('  <visual>')
    A('    <headlight ambient="0.4 0.4 0.4" diffuse="0.5 0.5 0.5" specular="0.1 0.1 0.1"/>')
    A('    <quality shadowsize="4096" offsamples="8"/>')
    A('    <global offwidth="1920" offheight="1080"/>')  # preview.py renders 1600x900
    A('    <map znear="0.005" zfar="40"/>')
    A('  </visual>')
    A('  <statistic center="%.4f %.4f %.4f" extent="1.5"/>'
      % tuple(v * S for v in layout["scene_centre"]))
    A('  <asset>')
    A('    <texture type="skybox" builtin="gradient" rgb1="0.13 0.15 0.19"'
      ' rgb2="0.02 0.02 0.03" width="256" height="256"/>')
    A('    <texture name="grid" type="2d" builtin="checker" rgb1="0.17 0.17 0.19"'
      ' rgb2="0.21 0.21 0.23" width="300" height="300"/>')
    A('    <material name="floor" texture="grid" texrepeat="8 8" reflectance="0.05"/>')
    A(mat("cabinet", "0.085 0.085 0.09 1", specular="0.4", shininess="0.6"))
    A(mat("white_key", "0.94 0.94 0.92 1", specular="0.18", shininess="0.3"))
    A(mat("black_key", "0.045 0.045 0.05 1", specular="0.35", shininess="0.55"))
    A(mat("pcb", "0.10 0.10 0.11 1", specular="0.2"))
    A(mat("led", "0.8 0.8 0.8 1", emission="0.9", specular="0.3"))
    A(mat("diffuser", "0.55 0.55 0.57 1", emission="0.95", specular="0.05"))
    A(mat("profile", "0.05 0.05 0.055 1", specular="0.25", shininess="0.4"))
    for name in sorted(piano["groups"]):
        A('    <mesh name="%s" file="%s.stl" scale="%g %g %g"/>' % (name, name, S, S, S))
    for name in ("led_chip", "strip_module"):
        A('    <mesh name="%s" file="%s.stl" scale="%g %g %g"/>' % (name, name, S, S, S))
    A('  </asset>')

    A('  <worldbody>')
    A('    <light name="key_light" pos="0 -1.3 2.1" dir="0 0.5 -1" directional="true"'
      ' diffuse="0.5 0.5 0.5" specular="0.12 0.12 0.12" castshadow="true"/>')
    A('    <light name="fill" pos="0 1.5 1.7" dir="0 -0.6 -1" directional="true"'
      ' diffuse="0.22 0.22 0.25" castshadow="false"/>')
    A('    <geom name="floor" type="plane" size="4 4 0.1" material="floor" pos="0 0 0"/>')

    A('    <body name="cabinet" pos="0 0 0">')
    geom("cabinet", "cabinet", "cabinet")
    A('    </body>')

    for k in keys:
        p = k["pivot"] * S
        size = (k["max"] - k["min"]) * S
        # Inertia is given explicitly: these key meshes are open shells, so the
        # volume MuJoCo would infer from them is not trustworthy, and a body with
        # a joint needs real mass. A thin box about the pivot is close enough for
        # a 10 mm travel at 3 deg.
        ix = k["mass"] * (size[1] ** 2 + size[2] ** 2) / 12.0
        iy = k["mass"] * (size[0] ** 2 + size[2] ** 2) / 12.0
        iz = k["mass"] * (size[0] ** 2 + size[1] ** 2) / 12.0
        com = (k["centre"] * S) - p
        A('    <body name="%s" pos="%.6f %.6f %.6f">' % (k["body"], p[0], p[1], p[2]))
        A('      <inertial pos="%.6f %.6f %.6f" mass="%.4f" diaginertia="%.3e %.3e %.3e"/>'
          % (com[0], com[1], com[2], k["mass"], ix, iy, iz))
        A('      <joint name="%s" type="hinge" axis="1 0 0" pos="0 0 0"'
          ' range="0 %.5f" damping="%g" armature="1e-5"/>'
          % (k["joint"], k["angle"], KEY_DAMPING))
        geom("geom_" + k["body"], k["mesh"], "black_key" if k["black"] else "white_key")
        A('    </body>')

    # These bodies carry a real frame, not 0 0 0: a targetbody camera aims at the
    # body's frame origin, so a bar whose geoms are offset from the world origin
    # made every "look at the bar" camera point at the floor instead.
    bar_mid = [(layout["tiles"][0]["led_centre"][0] + layout["tiles"][-1]["led_centre"][0]) / 2,
               layout["bar_y_centre_mm"], layout["bar_top_z_mm"]]
    A('    <body name="led_bar" pos="%.6f %.6f %.6f">'
      % (bar_mid[0] * S, bar_mid[1] * S, bar_mid[2] * S))
    for t in layout["tiles"]:
        geom("mod_%03d" % t["i"], "strip_module", "pcb")
        geom("led_%03d" % t["i"], "led_chip", "led",
             # Unlit must be darker than the dimmest drive the weighted model
             # produces (weight 0.25 -> PWM 12/255 = 0.047), or a faintly lit
             # LED reads as darker than an unlit one.
             extra=' rgba="0.03 0.03 0.035 1"')
    A('    </body>')

    d = layout["diffuser"]
    A('    <body name="diffuser" pos="%.6f %.6f %.6f">'
      % (bar_mid[0] * S, layout["bar_y_centre_mm"] * S, d["z_centre_mm"] * S))
    # Box geoms are not re-centred by MuJoCo the way meshes are, so these
    # positions are written RELATIVE to the body frame. Leaving them absolute
    # once the body got a frame of its own put the diffuser at z 217.7 mm.
    for i, cx in enumerate(d["centres_mm"]):
        A('      <geom name="diff_%04d" type="box" size="%.6f %.6f %.6f"'
          ' pos="%.6f 0 0" material="diffuser" rgba="0.055 0.055 0.06 1"'
          ' contype="0" conaffinity="0" group="1"/>'
          % (i, d["seg_len_mm"] / 2 * S, d["cap_width_mm"] / 2 * S,
             d["thickness_mm"] / 2 * S, (cx - bar_mid[0]) * S))
    # Side walls. Without them the emitters are visible from the front, under the
    # cap's leading edge -- a bare tape with a floating lid, not a profile.
    wall_h = d["wall_z_hi_mm"] - d["wall_z_lo_mm"]
    wall_zc = (d["wall_z_hi_mm"] + d["wall_z_lo_mm"]) / 2.0 - d["z_centre_mm"]
    bar_len = LED_PITCH_MM * len(layout["tiles"])
    for side, sign in (("front", -1.0), ("back", 1.0)):
        A('      <geom name="diffwall_%s" type="box" size="%.6f %.6f %.6f"'
          ' pos="0 %.6f %.6f" material="profile" contype="0" conaffinity="0"'
          ' group="1"/>'
          % (side, bar_len / 2 * S, WALL_THICK_MM / 2 * S, wall_h / 2 * S,
             sign * (d["cap_width_mm"] / 2 + WALL_THICK_MM / 2) * S, wall_zc * S))
    A('    </body>')

    for name, (pos, target) in layout["cameras"].items():
        fov = ' fovy="20"' if name == "bar_macro" else ""
        A('    <camera name="%s" pos="%.4f %.4f %.4f" mode="targetbody" target="%s"%s/>'
          % (name, pos[0] * S, pos[1] * S, pos[2] * S, target, fov))
    A('  </worldbody>')
    A('  <actuator>')
    for k in keys:
        A('    <position name="%s" joint="%s" kp="%g" kv="%g" ctrlrange="0 %.5f"/>'
          % (k["actuator"], k["joint"], KEY_KP, KEY_KV, k["angle"]))
    A('  </actuator>')
    A('</mujoco>')
    return "\n".join(L) + "\n"


def write_file(path, text):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(text.encode("utf-8"))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "scene.xml"))
    ap.add_argument("--align", choices=["centre", "map"], default=STRIP_ALIGN,
                    help="centre: fill the keyboard edge to edge (default); "
                         "map: start where tools/gen_map.py put the tape")
    a = ap.parse_args()

    import mujoco

    piano, strip = load_part("piano"), load_part("strip")
    keys = key_table(piano)
    layout = compute_layout(piano, strip, keys, a.align)

    # pass 1: everything at the origin, to learn what MuJoCo did to each mesh
    write_file(a.out, build_xml(piano, keys, layout))
    m = mujoco.MjModel.from_xml_path(a.out)

    # pass 2: pos = where the part belongs - where MuJoCo actually put its
    # vertices when asked for pos="0 0 0".
    #
    # MuJoCo does two things to a mesh asset that make a naive offset wrong, and
    # both were caught by the verifier rather than reasoned about in advance:
    #   * it re-centres the vertices on the mesh's centre of mass and *adds* the
    #     removed offset (`mesh_pos`) to the geom's pos, so subtracting only the
    #     AABB centre double-counts it -- keys landed at twice their x, a 2429 mm
    #     keyboard;
    #   * it re-orients the vertices onto the mesh's inertia frame (`mesh_quat`,
    #     e.g. 0.7176 0.6963 0.0114 -0.0004 for the bottom A key) and
    #     compensates with `geom_quat`, so the AABB must be taken *after*
    #     rotating -- ignoring that left every part off by 15-32 mm.
    # Measuring the compiled result at pos=0 sidesteps both.
    d0 = mujoco.MjData(m)
    mujoco.mj_forward(m, d0)
    tile_by_i = {t["i"]: t for t in layout["tiles"]}
    off = {}
    for gid in range(m.ngeom):
        if m.geom_type[gid] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        gname = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, gid)
        mid = m.geom_dataid[gid]
        adr, num = m.mesh_vertadr[mid], m.mesh_vertnum[mid]
        v = m.mesh_vert[adr:adr + num].astype(np.float64)
        w = v @ d0.geom_xmat[gid].reshape(3, 3).T + d0.geom_xpos[gid]
        at_zero = (w.min(axis=0) + w.max(axis=0)) / 2.0
        if gname.startswith("led_"):
            want = tile_by_i[int(gname[4:])]["led_centre"] * S
        elif gname.startswith("mod_"):
            want = tile_by_i[int(gname[4:])]["mod_centre"] * S
        else:
            mesh_name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_MESH, mid)
            lo, hi = stl_bounds(asset(mesh_name))
            want = (lo + hi) / 2.0 * S
        p = np.asarray(want) - at_zero
        off[gname] = "%.6f %.6f %.6f" % (p[0], p[1], p[2])

    write_file(a.out, build_xml(piano, keys, layout, off))

    keymap = {
        "source": "generated by sim/make_scene.py",
        "design_constants": {
            "gen_map_strip_origin_mm_from_A0_left": STRIP_ORIGIN_MM,
            "led_pitch_mm": LED_PITCH_MM, "design_led_count": DESIGN_LED_COUNT,
            "bar_from_rim_mm": BAR_FROM_RIM_MM,
        },
        "measured_from_cad": {k: layout[k] for k in (
            "a0_left_x", "c8_right_x", "playing_width_mm", "white_pitch_mm",
            "octave_span_mm", "key_tail_y", "key_front_y", "white_top_z",
            "black_top_z", "shelf_z", "shelf_area_mm2", "shelf_candidates",
            "shelf_rim_y",
            "strip_width_mm", "strip_thickness_mm", "chip_size_mm",
            "bar_y_front_mm", "bar_y_centre_mm", "bar_base_z_mm", "bar_top_z_mm",
            "bar_above_white_mm", "bar_above_black_mm", "bar_len_mm",
            "led_count", "strip_align", "strip_origin_from_a0_mm",
            "margin_left_mm", "margin_right_mm", "leds_that_fit",
            "diffuser", "diffuser_above_black_mm",
            "cad_strip_len_mm", "cad_led_count", "cad_module_pitch_mm")},
        "keys": [{"midi": k["midi"], "note": k["note"], "body": k["body"],
                  "mesh": k["mesh"], "black": k["black"],
                  "centre_x_mm": float(k["centre"][0]),
                  "joint": k["joint"], "actuator": k["actuator"],
                  "pivot_mm": [float(v) for v in k["pivot"]],
                  "arm_mm": k["arm_mm"], "angle_rad": k["angle"],
                  "dip_mm": KEY_DIP_MM, "mass_kg": k["mass"]} for k in keys],
        "leds": [{"index": t["i"], "x_mm": float(t["led_centre"][0]),
                  "y_mm": float(t["led_centre"][1]),
                  "z_mm": float(t["led_centre"][2])} for t in layout["tiles"]],
    }
    write_file(os.path.join(ASSETS, "keymap.json"), json.dumps(keymap, indent=1))

    print("scene  -> %s   (%d geoms, %d meshes, %d hinges, %d actuators)"
          % (a.out, m.ngeom, m.nmesh, m.njnt, m.nu))
    ang = [k["angle"] for k in keys]
    arm = [k["arm_mm"] for k in keys]
    print("keys   hinge %.2f..%.2f deg for a %.1f mm dip; pivot arm %.1f..%.1f mm"
          % (min(ang) * 180 / np.pi, max(ang) * 180 / np.pi, KEY_DIP_MM,
             min(arm), max(arm)))
    print("keymap -> %s" % os.path.join(ASSETS, "keymap.json"))
    print("CAD    playing width %.2f mm | white pitch %.4f mm | octave span %.2f mm"
          % (layout["playing_width_mm"], layout["white_pitch_mm"], layout["octave_span_mm"]))
    print("       key tails y=%.2f | white tops z=%.2f | black tops z=%.2f"
          % (layout["key_tail_y"], layout["white_top_z"], layout["black_top_z"]))
    print("board  z=%.2f mm (%.0f mm2 flat), front rim y=%.2f mm, %.2f mm in front of "
          "the key tails; other levels %s"
          % (layout["shelf_z"], layout["shelf_area_mm2"], layout["shelf_rim_y"],
             layout["key_tail_y"] - layout["shelf_rim_y"],
             [(z, round(ar)) for z, ar in layout["shelf_candidates"][1:]]))
    print("bar    %d LEDs at %.4f mm, x %.2f..%.2f, y %.2f, top z %.2f"
          % (layout["led_count"], LED_PITCH_MM, layout["tiles"][0]["led_centre"][0],
             layout["tiles"][-1]["led_centre"][0], layout["bar_y_centre_mm"],
             layout["bar_top_z_mm"]))
    print("       bar top sits %+.2f mm vs white key tops, %+.2f mm vs black key tops"
          % (layout["bar_above_white_mm"], layout["bar_above_black_mm"]))
    print("align  '%s': %d LEDs span %.2f mm of a %.2f mm keyboard, margins %+.2f mm "
          "left / %+.2f mm right; %d is the most that fit (%d would overhang by %.2f mm)"
          % (layout["strip_align"], layout["led_count"], layout["bar_len_mm"],
             layout["playing_width_mm"], layout["margin_left_mm"], layout["margin_right_mm"],
             layout["leds_that_fit"], layout["leds_that_fit"] + 1,
             (layout["leds_that_fit"] + 1) * LED_PITCH_MM - layout["playing_width_mm"]))
    mo = map_origin_mm()
    d = layout["strip_origin_from_a0_mm"] - mo
    print("       tape starts %+.2f mm from A0's left edge; data/led_map.json says "
          "%+.3f mm, a difference of %.2f mm = %.2f LED pitches"
          % (layout["strip_origin_from_a0_mm"], mo, d, d / LED_PITCH_MM))
    d = layout["diffuser"]
    print("diffuser %d segments of %.2f mm at z %.2f mm (%.1f mm above the LEDs, "
          "%+.2f mm vs black-key tops)"
          % (d["segments"], d["seg_len_mm"], d["z_centre_mm"], d["gap_mm"],
             layout["diffuser_above_black_mm"]))
    print("CAD strip on file: %.1f mm, %d LEDs at %.3f mm pitch -- the sim tiles that"
          " module %d times at %.4f mm"
          % (layout["cad_strip_len_mm"], layout["cad_led_count"],
             layout["cad_module_pitch_mm"], layout["led_count"], LED_PITCH_MM))
    return 0


if __name__ == "__main__":
    sys.exit(main())
