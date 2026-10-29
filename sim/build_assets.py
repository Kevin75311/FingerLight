"""STEP -> STL asset builder for the MuJoCo sim.

    python sim/build_assets.py --part piano
    python sim/build_assets.py --part strip

Writes one STL per part into `sim/assets/`, plus a manifest that make_scene.py
reads. STLs are written in millimetres (MuJoCo scales by 0.001 on load).

Tessellation goes through cascadio, which is OpenCASCADE's own `BRepMesh`, and
the result is read back with trimesh. That choice is worth a note, because the
obvious alternative was tried first and does not work here:

* gmsh reads the same STEP, but `importShapes` needs `highestDimOnly=False` (the
  piano holds exactly one closed solid, `Red_felt`, among 124 products, and the
  default silently imports that one alone -- 6 faces out of 42079), it drops the
  product names on import (41943 of 42079 faces come back unlabelled, so parts
  have to be re-clustered by shared curves), and meshing those 42079 faces one at
  a time ran for 17 minutes without finishing.
* cascadio converts the whole piano in ~26 s to 45k triangles and **keeps the
  STEP product names** (`White_key_01`..`52`, `Black_key_01`..`36`,
  `Top_control_panel`, `Key_rear_trim`, ...), which is exactly the grouping the
  scene needs.

The strip is 144 identical 7 mm modules, so only module 1 is exported, as
`led_chip` + `strip_module`; make_scene tiles it at the design LED pitch.

Group counts are asserted (52 white keys, 36 black keys, one LED package), so a
CAD change that breaks an assumption fails the build instead of quietly
producing a keyboard with 51 keys.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CAD = os.path.join(ROOT, "hardware", "cad")
ASSETS = os.path.join(HERE, "assets")

STEPS = {
    "piano": os.path.join(CAD, "Casio_PX-S1100_detailed.step"),
    "strip": os.path.join(CAD, "WS2812 144.step"),
}
# linear deflection in model units (mm): how far a chord may sag from the true
# surface. 0.25 mm keeps the key edges straight at any camera distance used here.
TOL_LINEAR = {"piano": 0.25, "strip": 0.06}
TOL_ANGULAR = {"piano": 0.5, "strip": 0.4}

WHITE_RE = re.compile(r"^White_key_(\d+)", re.I)
BLACK_RE = re.compile(r"^Black_key_(\d+)", re.I)
N_WHITE, N_BLACK = 52, 36
MODULE_PITCH_MM = 7.0          # the CAD strip's own module pitch, measured below


def to_glb(part, out_dir, no_cache):
    import cascadio

    cache_dir = os.path.join(out_dir, "_cache")
    os.makedirs(cache_dir, exist_ok=True)
    glb = os.path.join(cache_dir, "%s.glb" % part)
    if os.path.exists(glb) and not no_cache:
        print("using cached %s" % os.path.basename(glb), flush=True)
        return glb
    t0 = time.time()
    print("tessellating %s ..." % os.path.basename(STEPS[part]), flush=True)
    cascadio.step_to_glb(STEPS[part], glb + ".tmp",
                         tol_linear=TOL_LINEAR[part], tol_angular=TOL_ANGULAR[part])
    os.replace(glb + ".tmp", glb)
    print("tessellate: %.1f s -> %s" % (time.time() - t0, os.path.basename(glb)), flush=True)
    return glb


def load_parts(glb):
    """Every placed node as (name, mesh-in-mm). Instances are expanded."""
    import trimesh

    scene = trimesh.load(glb)
    parts = []
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph.get(node)
        mesh = scene.geometry[gname].copy()
        mesh.apply_transform(T)
        mesh.apply_scale(1000.0)          # glTF is metres, this pipeline is mm
        parts.append((gname, mesh))
    return parts


def group_piano(parts):
    whites, blacks, cabinet = {}, {}, []
    for name, mesh in parts:
        m = WHITE_RE.match(name)
        if m:
            whites[int(m.group(1))] = mesh
            continue
        m = BLACK_RE.match(name)
        if m:
            blacks[int(m.group(1))] = mesh
            continue
        cabinet.append(mesh)
    if len(whites) != N_WHITE or len(blacks) != N_BLACK:
        raise SystemExit("expected %d white / %d black keys, found %d / %d"
                         % (N_WHITE, N_BLACK, len(whites), len(blacks)))
    groups = {}
    # Renumber both families left to right: the CAD's own numbering is not
    # guaranteed to run that way, and every downstream note assignment depends
    # on x order.
    for i, k in enumerate(sorted(whites, key=lambda k: whites[k].bounds[0][0]), 1):
        groups["white_key_%02d" % i] = [whites[k]]
    for i, k in enumerate(sorted(blacks, key=lambda k: blacks[k].bounds[0][0]), 1):
        groups["black_key_%02d" % i] = [blacks[k]]
    groups["cabinet"] = cabinet
    return groups, {}


def group_strip(parts):
    """Export one 7 mm module: its LED package, and everything else in the tile."""
    x0 = min(mesh.bounds[0][0] for _, mesh in parts)
    module = [(n, m) for n, m in parts if m.bounds[0][0] < x0 + MODULE_PITCH_MM - 1e-6]
    chip = [m for n, m in module if "LED Chip" in n]
    rest = [m for n, m in module if "LED Chip" not in n]
    if not chip:
        raise SystemExit("no 'WS2812 LED Chip' geometry in the first module")

    pcb_x = sorted({round(m.bounds[0][0], 3) for n, m in parts if "PCB Strip" in n})
    pitches = np.diff(pcb_x) if len(pcb_x) > 1 else np.array([0.0])
    extra = {
        "cad_led_count": len(pcb_x),
        "cad_module_pitch_mm": float(np.median(pitches)),
        "cad_module_pitch_spread_mm": float(pitches.ptp()),
        "cad_strip_bbox": {
            "min": [float(min(m.bounds[0][i] for _, m in parts)) for i in range(3)],
            "max": [float(max(m.bounds[1][i] for _, m in parts)) for i in range(3)],
        },
        "module_x0_mm": float(x0),
    }
    return {"led_chip": chip, "strip_module": rest}, extra


def write_group(name, meshes, out_dir):
    import trimesh

    merged = trimesh.util.concatenate(meshes)
    path = os.path.join(out_dir, name + ".stl")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(trimesh.exchange.stl.export_stl(merged))
    os.replace(tmp, path)
    return path, merged


def build(part, out_dir, no_cache=False):
    os.makedirs(out_dir, exist_ok=True)
    glb = to_glb(part, out_dir, no_cache)
    t0 = time.time()
    parts = load_parts(glb)
    tris = sum(len(m.faces) for _, m in parts)
    print("%d placed parts, %d triangles (%.1f s)" % (len(parts), tris, time.time() - t0),
          flush=True)

    groups, extra = group_piano(parts) if part == "piano" else group_strip(parts)

    manifest = {
        "part": part,
        "source_step": os.path.relpath(STEPS[part], ROOT).replace("\\", "/"),
        "units": "mm",
        "tol_linear_mm": TOL_LINEAR[part],
        "tol_angular_rad": TOL_ANGULAR[part],
        "placed_parts": len(parts),
        "groups": {},
    }
    manifest.update(extra)

    total = 0
    for name in sorted(groups):
        path, merged = write_group(name, groups[name], out_dir)
        total += len(merged.faces)
        lo, hi = merged.bounds
        manifest["groups"][name] = {
            "stl": "assets/%s.stl" % name,
            "meshes": len(groups[name]),
            "triangles": int(len(merged.faces)),
            "bbox_min": [float(v) for v in lo],
            "bbox_max": [float(v) for v in hi],
            "center": [float(v) for v in (lo + hi) / 2.0],
        }
    print("wrote %d STLs, %d triangles" % (len(groups), total), flush=True)

    manifest["total_triangles"] = total
    mpath = os.path.join(out_dir, "%s_manifest.json" % part)
    with open(mpath + ".tmp", "wb") as f:
        f.write(json.dumps(manifest, indent=1).encode("utf-8"))
    os.replace(mpath + ".tmp", mpath)
    print("manifest -> %s" % mpath, flush=True)
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=sorted(STEPS), required=True)
    ap.add_argument("--out", default=ASSETS)
    ap.add_argument("--no-cache", action="store_true", help="re-tessellate the STEP")
    a = ap.parse_args()
    build(a.part, a.out, a.no_cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
