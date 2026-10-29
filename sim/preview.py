"""Render (or fly around) the MuJoCo scene, with the bar lit by the real firmware.

    python sim/preview.py                      # render every camera to sim/renders/
    python sim/preview.py --cue "C4:R:1,E4:R:2,G4:R:3"
    python sim/preview.py --viewer             # interactive window

The lighting is not faked here. `--cue` goes through
`fingerlight.notemap.note_to_leds` and `fingerlight.colors.pair_colors`, the same
two functions the Pi will call, so what the render shows is what the firmware
would push to the strip -- including the black-key gain and the hand/finger pixel
pairing. A wrong LED here is a wrong LED on the bar.

Cue syntax: `NOTE:HAND:FINGER`, e.g. `C4:R:1`, `F#3:L:5`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCENE = os.path.join(HERE, "scene.xml")
RENDERS = os.path.join(HERE, "renders")
sys.path.insert(0, os.path.join(ROOT, "firmware", "src"))

NOTE_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d)$")
SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def midi_of(name):
    m = NOTE_RE.match(name.strip())
    if not m:
        raise SystemExit("bad note %r (want e.g. C4, F#3, Bb2)" % name)
    step, acc, octv = m.group(1).upper(), m.group(2), int(m.group(3))
    return (int(octv) + 1) * 12 + SEMI[step] + (1 if acc == "#" else -1 if acc == "b" else 0)


def frame_from_cues(cue_str, n_leds, palette=None):
    """Turn a cue string into an n_leds x 3 uint8 frame, via the firmware."""
    from fingerlight import colors, notemap

    settings = notemap.StripSettings.load()
    frame = np.zeros((n_leds, 3), dtype=np.uint8)
    used = []
    for token in filter(None, (t.strip() for t in cue_str.split(","))):
        parts = token.split(":")
        if len(parts) != 3:
            raise SystemExit("bad cue %r (want NOTE:HAND:FINGER)" % token)
        midi = midi_of(parts[0])
        hand, finger = parts[1].upper(), int(parts[2])
        hand_led, finger_led = notemap.note_to_leds(midi, settings)
        hand_rgb, finger_rgb = colors.pair_colors(hand, finger, midi, palette=palette)
        for led, rgb in ((hand_led, hand_rgb), (finger_led, finger_rgb)):
            if 0 <= led < n_leds:
                frame[led] = rgb
        used.append((parts[0], midi, hand, finger, hand_led, finger_led))
    return frame, used


def apply_frame(model, frame, off=(0.05, 0.05, 0.05)):
    """Push an RGB frame onto the led_* geoms. Returns how many geoms were lit."""
    import mujoco

    lit = 0
    for i in range(len(frame)):
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "led_%03d" % i)
        if gid < 0:
            continue
        r, g, b = (int(v) for v in frame[i])
        if r or g or b:
            model.geom_rgba[gid] = [r / 255.0, g / 255.0, b / 255.0, 1.0]
            lit += 1
        else:
            model.geom_rgba[gid] = [off[0], off[1], off[2], 1.0]
    return lit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default=SCENE)
    ap.add_argument("--cue", default="", help="NOTE:HAND:FINGER, comma separated")
    ap.add_argument("--palette", default=None, help="ramp | distinct | anchors")
    ap.add_argument("--cameras", default="", help="comma separated; default all")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--out", default=RENDERS)
    ap.add_argument("--tag", default="", help="suffix for the PNG names")
    ap.add_argument("--viewer", action="store_true", help="open the interactive viewer")
    a = ap.parse_args()

    import mujoco

    model = mujoco.MjModel.from_xml_path(a.scene)
    data = mujoco.MjData(model)

    n_leds = sum(1 for i in range(model.ngeom)
                 if (mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, i) or "").startswith("led_"))
    if a.cue:
        frame, used = frame_from_cues(a.cue, n_leds, a.palette)
        lit = apply_frame(model, frame)
        for note, midi, hand, finger, hl, fl in used:
            print("%-4s midi %3d  %s%d -> hand LED %3d, finger LED %3d"
                  % (note, midi, hand, finger, hl, fl))
        print("%d LED geoms lit of %d" % (lit, n_leds))

    mujoco.mj_forward(model, data)

    if a.viewer:
        import mujoco.viewer
        mujoco.viewer.launch(model, data)
        return 0

    os.makedirs(a.out, exist_ok=True)
    cams = [c for c in (a.cameras.split(",") if a.cameras else
                        [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_CAMERA, i)
                         for i in range(model.ncam)]) if c]
    renderer = mujoco.Renderer(model, a.height, a.width)
    for cam in cams:
        renderer.update_scene(data, camera=cam)
        img = renderer.render()
        name = "%s%s.png" % (cam, ("_" + a.tag) if a.tag else "")
        path = os.path.join(a.out, name)
        _write_png(path, img)
        print("%-12s -> %s  (mean luma %.1f)" % (cam, path, img.mean()))
    return 0


def _write_png(path, img):
    """PNG writer with no image library: zlib + a hand-rolled chunk stream."""
    import struct
    import zlib

    h, w, _ = img.shape
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(img[y].tobytes())
    out = bytearray(b"\x89PNG\r\n\x1a\n")

    def chunk(tag, payload):
        out.extend(struct.pack(">I", len(payload)))
        out.extend(tag + payload)
        out.extend(struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    chunk(b"IEND", b"")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(out))
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
