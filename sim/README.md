# Part 4 · Simulation — the bar and the piano in MuJoCo

A MuJoCo scene built from the two CAD files in `hardware/cad/`, lit by the real
firmware. It exists so the cue path (score → note → LED → colour) can be watched
end to end before the bar is built, and so the note→LED map can be checked
against the instrument's own geometry rather than against a spreadsheet.

```
python sim/build_assets.py --part piano     # STEP -> STL, ~25 s
python sim/build_assets.py --part strip     # STEP -> STL, ~2 s
python sim/make_scene.py                    # -> sim/scene.xml + assets/keymap.json
python sim/verify_scene.py                  # 16 checks
python sim/make_scene.py --align map        # strip where led_map.csv expects it
python sim/preview.py --cue "C4:R:1,E4:R:2,G4:R:3,C3:L:5"
python sim/preview.py --viewer              # interactive, static
python sim/play.py                          # live viewer, plays a light-score
python sim/play.py --selftest               # headless playback with assertions
python sim/keysweep.py                      # press every key A0..C8 in turn
python sim/keysweep.py --selftest           # the same sweep, asserted, headless
python sim/keysweep.py --light weighted --diffuser      # the continuous model
python sim/lightfield.py                    # its measured blob metrics
```

`sim/scene.xml`, `sim/assets/*` and `sim/renders/*` are **generated**. Fix
`make_scene.py`, never the XML.

## What is in the scene

| | |
|---|---|
| Geoms / meshes | 442 / 91 |
| Piano | `cabinet` + 88 key bodies, 44 971 triangles |
| Key bodies | `key_A0` … `key_C8`, named for the MIDI note they sound |
| Key action | one hinge + position actuator each: `j_key_C4`, `act_key_C4` |
| Bar | the CAD's own 7 mm WS2812 module, tiled 176x at 6.9444 mm, centred on the keyboard |
| LED geoms | `led_000` … `led_175` — index n **is** index n of `data/led_map.csv` |
| Cameras | `player`, `overhead`, `bar_closeup`, `left_end` |

`preview.py --cue NOTE:HAND:FINGER` does not fake the colours: it calls
`fingerlight.notemap.note_to_leds` and `fingerlight.colors.pair_colors`, the two
functions the Pi will call. A wrong LED in a render is a wrong LED on the bar.

## What the CAD says, measured from the meshes

Every number below is printed by `make_scene.py` / `verify_scene.py`; none of it
was read off a viewer.

| Quantity | Value | Against the design |
|---|---:|---|
| Playing width (A0 left edge → C8 right edge) | 1226.21 mm | 1224.23 mm at M1 |
| White-key pitch (left edges) | 23.4428 … 23.7672 mm | 23.5429 mm, uniform |
| Octave span implied by the CAD | 165.07 mm | M1 measured 164.8 mm (+0.27) |
| White-key tops / black-key tops | z 90.71 / 100.77 mm | — |
| Top board (largest flat level over the keys) | z 98.00 mm, 40 779 mm² | the bar's shelf |
| Front rim of that board | y 49.09 mm | 21.32 mm **in front of** the key tails |
| Bar, laid against the rim | y 49.09 … 61.03 mm | top z 100.10 mm |
| Clearance under the bar | +9.85 mm | highest key geometry there is D3 at 88.15 mm |
| LEDs that fit the playing width | **176** | 177 would overhang by 2.95 mm |
| Strip margins, centred | +2.00 mm each end | tape starts +2.00 mm from A0's left edge |

Four things that came out of this and are worth knowing:

1. **The replica's keys are not evenly pitched.** Every white key is exactly
   22.604 mm wide, but the left-edge pitch varies by 0.324 mm. That spread is
   identical at tessellation tolerances of 0.25, 0.05 and 0.01 mm, so it is in
   the CAD, not in the mesh. The real instrument is the authority here (M1), not
   this replica.
2. **The board overhangs the key tails by 21.32 mm.** The keys run *under* it,
   so the bar is anchored on the board's front rim (`BAR_FROM_RIM_MM = 0`), not
   on the key tails — as far toward the player as the board allows. Laid flat
   there the LED tops are at z 100.10 mm, 0.67 mm *below* the black-key tops:
   option A in [[hardware]], "LEDs graze keys, black keys cast shadows". The
   10–15 mm clearance the hardware note prefers needs the printed wedge
   (option B).
3. **The strip is centred, and the map was regenerated to match** (2026-09-21).
   `make_scene.py --align centre` (the default) fills the keyboard edge to edge:
   176 LEDs, 2.00 mm of margin at each end. `tools/gen_map.py` originally chose a
   tape origin of -7.75 mm from A0's left edge -- picked to minimise worst-case
   centre error, not for symmetry, which left 9.76 mm bare at the top end and was
   the visible left shift. `tools/gen_map.py 164.8 --origin centre` regenerates
   the map for a centred tape: worst-case centre error 3.40 -> **3.45 mm**, still
   **88/88 unique LEDs**, and `shift`/`note_offsets` in
   `firmware/config/settings_overlay.json` updated with it. `--align map` still
   reproduces the old placement, and `verify_scene.py` then fails -- correctly --
   because the map no longer matches that position.
4. **The LED-to-key offset splits in two**, and the verifier reports both: the
   map's offset against where the tape actually starts, and this replica's
   geometry vs the M1 model (−4.48 … +0.46 mm, mean −2.13). With `--align map`
   the first term is just the map's own quantisation, |max| 3.40 mm — exactly
   the floor `data/led_map.csv` predicts — and the worst total is −6.68 mm at
   E4. Nothing in the map should be changed off the geometry term: the CAD
   replica is not the instrument.

The strip CAD on file is a 1008 mm part carrying 144 LEDs at a nominal 7.000 mm
pitch. The design bar is 176 LEDs at 6.9444 mm over 1222.21 mm, so the sim tiles
that module 176 times at the design pitch rather than using the file's length.

## How the parts get in

`build_assets.py` tessellates with cascadio (OpenCASCADE `BRepMesh`) and reads
the result back with trimesh. gmsh was tried first and is the wrong tool for
this pair of files: it needs `highestDimOnly=False` (the piano holds exactly one
closed solid among 124 products, so the default imports 6 faces out of 42 079),
it loses the STEP product names on import, and meshing those 42 079 faces ran 17
minutes without finishing. cascadio does the piano in ~24 s and keeps
`White_key_01`…`Black_key_36` as geometry names.

## Placement, and why `make_scene.py` compiles twice

MuJoCo re-centres each mesh on its centre of mass **and** re-orients it onto its
inertia frame, compensating with `geom_pos` / `geom_quat`. So a geom asked for
`pos="0 0 0"` does not put its vertices at the CAD origin. `make_scene.py`
therefore compiles once with everything at the origin, measures where each mesh
actually landed (rotation included), and re-emits with the difference. Getting
either half of that wrong is not subtle — ignoring the offset produced a 2429 mm
keyboard, ignoring the rotation left every part 15–32 mm out — and both were
caught by `verify_scene.py`, not by looking at a render.

## Verification

`verify_scene.py` re-derives everything from the compiled model and the STLs; it
does not import `make_scene`, so a bug in the generator cannot hide in the
check. 15 checks, each printing the number it measured. 14 pass; `map matches
strip origin` fails by design while the strip is centred and the map is not —
see point 3 above.

`python sim/verify_scene.py --canary` displaces two keys through the compiler —
C4 by 1.0 mm sideways and C#4 by 20 mm up into the bar's footprint — and the
placement and clearance checks report exactly those, which is how those checks
are known to be able to fail. The clearance check needed that canary: its first
version tested key *vertices* inside the bar's 12 mm band, and these key meshes
are boxes whose vertices sit only at the corners, so it examined nothing on all
88 keys and reported a clearance of +1e9 mm. It now tests triangles that span
the band (664 of them).

## Playback

`sim/play.py` runs a light-score against the scene in real time. The score is
parsed by `fingerlight.lightscore`, the cursor is a real
`fingerlight.practice.PracticeSession`, and each displayed frame is that
session's own `frame().pixels` -- the LED-index -> RGB dict the Pi would push to
the strip. play.py only decides *when* the virtual player hits each note.

* `--speed 0.5`, `--loop`, `--preview N` (how many upcoming events to dim-light),
  `--palette ramp|distinct|anchors`;
* `--miss N` fumbles every Nth event (a neighbouring semitone, 60 ms early) so
  the wrong-note path in the state machine actually runs;
* `--selftest` replays headless at 100 Hz and asserts that every event's own LEDs
  are lit while it is current, that the cursor reaches the end, and that the lit
  set changes over time -- a frozen bar would pass each per-frame check alone;
* `--frames DIR --fps F` writes an offscreen PNG sequence. Measured on the
  C-major example: the lit region travels 523 -> 625 px across the take and goes
  dark after the last event, which is the ascending scale.

## The key action

Every key is a hinge about a balance rail 8 mm in front of its own rear end, at
its mid-height, with a position actuator on it. The hinge angle is **per key**,
not shared: it is whatever gives *that* key's front tip the same 10 mm travel,
which is 3.46° for the longest white key and 5.01° for the shortest black one
(their pivot-to-tip arms are 165.5 and 114.0 mm).

**Gravity is off in this scene, deliberately.** Each key's centre of mass sits in
front of its pivot, so weight torques it straight into its own travel: with
gravity on every key sagged 0.023 rad — 4.3 mm at the tip — and never returned.
A real key is held up by its return spring against a front-rail stop; nothing
else here has contacts or dynamics worth simulating, so the honest cheap model is
a weightless key whose actuator alone decides where it sits.

Gains are measured, not guessed (`kp=5, kv=0.08, damping=0.01`, timestep 2 ms):
a press reaches **97.2 % of travel in 50 ms and 100 % by 100 ms**, and a release
is back to **0.04 % in 100 ms**. `--hold`/`--release` default to 150 ms each.

`sim/keysweep.py` walks A0 → C8, pressing each key and lighting the LEDs that key
owns — straight from `notemap.note_to_leds` + `colors.pair_colors`, so nothing in
the sweep knows which LED belongs to which key. `--selftest` asserts, per note,
from the stepped model:

* the key that moved is the one named for that note, and no other key moved;
* its front tip dipped 8.5–10.5 mm (measured across all 88: **9.86–9.96 mm**);
* exactly the LEDs the firmware named are lit;
* the lit pair is the one physically nearest that key — **0.01–3.53 mm, mean
  1.55 mm** across the keyboard, with a mean limit of 3.0 mm.

That last limit is what catches a misaligned tape rather than a bad key: running
the same sweep against `make_scene.py --align map` (the tape 1.24 LED pitches
off) gives mean 5.33 mm and fails, while every per-note dip still passes.

## Continuous mapping + diffuser (prototype, sim only)

`sim/lightfield.py` is a **proposal**, not a shipped change: it touches no
firmware, no `data/led_map.csv`, no settings overlay. It reads the same geometry
those are built from and drives the tape differently, so the two can be compared.

**The drive.** Treat the tape as a continuous line of 6.9444 mm cells. Each key
owns a band centred on its slot (`TARGET_SLOT_FRAC = 0.7` of 13.73 mm for
C1-B7); a LED's weight is how much of its cell falls inside that band, normalised
so the peak is 1. Then the diffuser blurs it, once. An earlier version sampled an
*already blurred* profile and let the optics blur it again -- 16.5 mm of light on
a 13.73 mm key. The drive must carry the sharp band.

**The optics.** `SIGMA_MM = 0.45 x DIFFUSER_GAP_MM`. The gap is not a taste
setting: `ripple()` drives every LED equally and measures how much the summed
field still undulates -- literally "can I see the dots".

| gap | sigma | ripple |
|---:|---:|---:|
| 4 mm | 1.80 | **69 %** |
| 5 mm | 2.25 | **39 %** |
| 6 mm | 2.70 | 12 % |
| **7 mm** | **3.15** | **0.0 %** |

So the bar cannot be made narrower by lowering the cap; below ~7 mm it stops
being a stripe and becomes dots. That is the same conclusion as the 6-10 mm
recommendation in [[hardware]], arrived at from the geometry.

**Measured** (`python sim/lightfield.py`), at gap 7 mm:

| drive | LEDs/key | FWHM | FWHM/slot | centre err | rms | crosstalk |
|---|---:|---:|---:|---:|---:|---:|
| **band 70 % of slot** | 1.90 | **11.3 mm** | **0.82** | **0.68 mm** | 0.080 | 1 % |
| band 85 % | 2.20 | 12.9 mm | 0.92 | 1.21 mm | 0.064 | 2 % |
| band 100 % | 2.47 | 14.7 mm | 1.06 | 1.33 mm | 0.060 | 3 % |
| today's fixed pair | 2.00 | 14.4 mm | 1.04 | **6.85 mm** | 0.251 | 16 % |
| *control: same drive, +1 LED* | - | 11.3 mm | - | *7.32 mm* | *0.371* | - |

`centre err` is the midpoint of the half-maximum span against where the key is --
not the argmax, which wanders half a pitch on a flat-topped blob and reported an
8 mm error for a blob centred within 1 mm. `rms` is the shape error against the
intended profile. The control row is the same key driven one LED to the right: if
those two columns did not move, they would not be measuring anything.

**In the scene** the bar is a channel, not a floating lid: 528 cap segments of
2.31 mm coloured by the summed light field, plus two side walls from the tape up
to the cap. Without the walls the emitters were visible under the cap's leading
edge. The cap sits 7 mm above the LEDs (z 107.85 mm, 7.83 mm above the black-key
tops). MuJoCo has no light transport, so this is a visual model: shape and
relative brightness are meaningful, absolute brightness is not.

`WEIGHT_FLOOR = 0.25` is where a WS2812 gives up: after gamma 2.2 that is PWM
12/255, while 0.18 is PWM 6 -- dimmer than an unlit package looks.

**What it still cannot do: adjacent semitones merge.** 11.3 mm FWHM against a
13.73 mm slot pitch, and C4/C#4 land on adjacent LED runs, so lit together they
read as one long glow (`sim/renders/semitone/`). Different finger colours keep
them apart; the unified colour used here does not.

Renders: `cmp_pair/`, `cmp_weighted/`, `cmp_diffused/` (one key), `chord_diffused/`
(C-E-G, three separate blobs), `semitone/` (C4+C#4, merged).

**Before any of this ships** it needs decisions that are not the sim's to make:
the map format becomes `note -> [(led, weight)]`, which upstream's
`shift`/`note_offsets` cannot express; and the hand/finger two-pixel encoding has
to be replaced, since a blob cannot be split into two roles.

## Not done yet

* No hand or finger geometry, so "which finger" is only a colour today.
* `play.py` does not move the keys yet — the sweep does. Wiring the score's note
  events to the actuators is the next small step.
* The bar is rendered with emissive geoms; MuJoCo does not bleed that light onto
  the keys, so the render shows which LED is lit, not how the key will look lit.
