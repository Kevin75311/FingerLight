---
name: simulation
description: Part 4 — the piano and the bar in MuJoCo, lit by the real firmware; what it has already caught
date: 2026-09-21
aliases: [sim, Simulation, part4, mujoco]
tags: [simulation, part4]
---

# Part 4 — the simulation

A MuJoCo scene built from the two CAD files in `hardware/cad/`, driven by the real
firmware. It exists so the cue path (score → note → LED → colour) can be watched end to
end before the bar is built, and so the note→LED map can be checked against the
instrument's own geometry instead of against a spreadsheet.

Runnable detail — flags, file layout, how to regenerate — lives in
[the sim README](../sim/README.md). This note is what the rest of the vault needs to know.

```
python sim/build_assets.py --part piano     # STEP -> STL, ~25 s
python sim/build_assets.py --part strip
python sim/make_scene.py                    # -> sim/scene.xml, sim/assets/keymap.json
python sim/verify_scene.py                  # 16 checks
python sim/keysweep.py --light weighted --diffuser   # press every key, watch the bar
python sim/play.py                          # play a light-score in real time
python sim/lightfield.py                    # the continuous-mapping numbers
```

## What is in it

| | |
|---|---|
| Piano | `cabinet` + 88 key bodies, 44 971 triangles, from `Casio_PX-S1100_detailed.step` |
| Keys | named for the note they sound (`key_A0` … `key_C8`), each on a hinge + position actuator |
| Key action | per-key angle for a common 10 mm front-tip dip: 3.46° (longest white) … 5.01° (shortest black) |
| Bar | the CAD's own 7 mm WS2812 module tiled 176× at 6.9444 mm, `led_000` … `led_175` |
| Diffuser | a channel: 528 cap segments of 2.31 mm + side walls, 7 mm above the LEDs |
| Cameras | `player`, `overhead`, `bar_closeup`, `left_end`, `bar_macro` |

Colours are **not** faked: `sim/play.py` and `sim/keysweep.py` call
`fingerlight.notemap.note_to_leds` and `fingerlight.colors.pair_colors`, the two functions
the Pi will call. A wrong LED in a render is a wrong LED on the bar.

## What it measured about the instrument

All from the CAD replica, printed by `make_scene.py` / `verify_scene.py`:

| Quantity | Value | Against the design |
|---|---:|---|
| Playing width | 1226.21 mm | 1224.23 mm at M1 |
| White-key pitch, left edges | 23.4428 … 23.7672 mm | 23.5429 mm, uniform |
| Octave span implied by the CAD | 165.07 mm | M1 measured 164.8 mm (+0.27) |
| White / black key tops | z 90.71 / 100.77 mm | — |
| Top board, largest flat level | z 98.00 mm, 40 779 mm² | the bar's shelf |
| Front rim of that board | y 49.09 mm | **21.32 mm in front of the key tails** |
| Bar laid against the rim | y 49.09 … 61.03 mm, top z 100.10 mm | −0.67 mm vs black-key tops |
| Clearance under the bar | +9.85 mm | tallest thing under it is D3's rear ramp |

Three things worth carrying into [hardware](hardware.md):

1. **The board overhangs the key tails by 21.32 mm** — the keys run *under* it, so the bar
   is anchored on the board's front rim, not on the tails. Anchoring on the tails parked it
   21 mm further from the player than it needs to be.
2. **The replica's keys are not evenly pitched**: every white key is exactly 22.604 mm wide
   but the left-edge pitch varies by 0.324 mm. Identical at tessellation tolerances of
   0.25, 0.05 and 0.01 mm, so it is in the CAD, not the mesh. The instrument (M1), not the
   replica, remains the authority.
3. **Flat on the board puts the LEDs level with the black keys** (−0.67 mm) — that is
   option A in [hardware](hardware.md) §3 with its known shadow problem. The diffuser channel raises
   the lit surface to 7.83 mm above the black keys, which is option B territory without the
   printed wedge.

## What it caught that nothing else would have

- **The tape was visibly off-centre.** `gen_map.py` chose its origin to minimise worst-case
  centring error, which left 9.76 mm bare at the top end. The map was regenerated centred;
  see [hardware](hardware.md) §2.1a. Cost: 0.05 mm of worst-case error.
- **A sign bug in `gen_map.py`'s PLV translation** (`shift = -d(21)`, it returned `+d(21)`),
  invisible for every map the tool had ever produced because they all had `shift = 0`.
  Surfaced the moment a centred map needed a non-zero shift — [log](../log.md) 2026-09-21.
- **The LED-to-key offset splits in two**, and `verify_scene.py` reports the halves
  separately: the map's own quantisation, and this CAD replica's geometry differing from
  the M1 model (−4.48 … +0.46 mm). Nothing in the map should be changed off the second.

## The continuous-mapping prototype

`sim/lightfield.py` proposes replacing the fixed 2-pixel pair with a weighted profile, now
that a diffuser makes the bar read as a stripe rather than dots. **It changes nothing
outside `sim/`** — not `data/led_map.csv`, not the firmware, not the overlay.

Each key owns a band (70 % of its slot); an LED's weight is how much of its 6.9444 mm cell
falls inside that band; the diffuser blurs the result once. Measured at a 7 mm gap
(σ 3.15 mm):

| drive | LEDs/key | FWHM | FWHM/slot | centre err | rms | crosstalk |
|---|---:|---:|---:|---:|---:|---:|
| **band 70 % of slot** | 1.90 | 11.3 mm | 0.82 | **0.68 mm** | 0.080 | 1 % |
| today's fixed pair | 2.00 | 14.4 mm | 1.04 | **6.85 mm** | 0.251 | 16 % |
| *control: drive shifted +1 LED* | – | 11.3 mm | – | *7.32 mm* | *0.371* | – |

**The gap is set by continuity, not taste.** Driving every LED equally and measuring how
much the summed field still undulates: 4 mm → 69 %, 5 mm → 39 %, 6 mm → 12 %, 7 mm → 0.0 %.
Below ~7 mm the dots show through the diffuser, which is the same conclusion as the 6–10 mm
recommendation in [hardware](hardware.md) §5, reached from the geometry. The blob therefore cannot be
narrowed by lowering the cap — only by narrowing the band.

Known limit: **adjacent semitones merge** (11.3 mm FWHM against a 13.73 mm slot pitch).
Different finger colours keep them apart; a single colour does not.

Open before any of it ships, both in [decisions](decisions.md): the map format would become
`note → [(led, weight)]`, which upstream's `shift`/`note_offsets` cannot express (Q-10),
and the hand/finger two-pixel encoding has to be replaced because a blob cannot carry two
roles (Q-11). The blur coefficient itself is unmeasured (Q-12).

## How far to trust it

- **VERIFIED**: mesh placement is exact to 0.0000 mm against the STL files; 16/16 checks in
  `verify_scene.py`, each printing the number it measured; `--canary` displaces two keys and
  the placement and clearance checks report exactly those displacements; the 88-key sweep
  asserts, per note, that the right key moved, that its tip dipped 9.86–9.96 mm, and that
  the LEDs the firmware named are lit and sit over that key.
- **ASSUMED**: the 10 mm key travel and the pivot 8 mm in front of the key's rear end are
  plausible piano-action figures, not measurements of this instrument; key masses are
  nominal; `σ = 0.45 × gap` is a modelling coefficient, not the real profile's scatter.
- **NOT MODELLED**: light transport (the diffuser's glow is computed, not ray-traced), the
  capacitive touch panel ([decisions](decisions.md) D-09), and anything electrical.

## See also

[index](index.md) · [hardware](hardware.md) for the geometry it measures against · [firmware](firmware.md) for the
functions it drives · [decisions](decisions.md) for what it left open · [log](../log.md) 2026-09-21 for the
session trail · [sim/README.md](../sim/README.md) for flags and file layout.
