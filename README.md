# PX-S1100 Finger-Cue LED Bar

An LED bar behind the keys of a Casio Privia PX-S1100 that shows which key to press and
**which finger to use** — colour encodes the finger. It reads a score, lights the way
through it, and listens back over MIDI or audio to know what you actually played.

Five parts: the **bar** (176 LEDs + a Raspberry Pi 5 with a 5" touchscreen), the
**firmware** that drives it, a
PC-side **score compiler** that turns sheet music into a playable light score, and a
**MuJoCo simulation** of the whole thing, driven by that same firmware.

Built on [onlaj/Piano-LED-Visualizer](https://github.com/onlaj/Piano-LED-Visualizer) (MIT).
What comes from there, what comes from ScoreForge, and what is new: [[provenance]].

> **Building today? Start at [[build]]** — 176 LEDs, data on GPIO10, and a staged
> bring-up where every step has a test: `tools/hw/preflight.py`, then
> `tools/hw/led_test.py stages`. **Flashing the Pi and the touchscreen: [[pi-setup]].**

## State

| Part | State |
|---|---|
| **1 · Hardware** — geometry, LEDs, power, wiring, BOM | Designed. **Not built.** [[hardware]] |
| **2 · Firmware** — note map, colour, practice, calibration, MIDI, audio | Logic complete; wired into the app's main loop (LEDs, MIDI, mic). **Never run on a Pi.** [[firmware]] |
| **3 · Score compiler** — MusicXML/OMR → hands → fingering → light score | Complete. [[score]] |
| **4 · Simulation** — piano + bar in MuJoCo, lit by the firmware | Keys hinged, bar diffused, 16/16 checks. [[simulation]] |
| **5 · App** — touchscreen UI on the Pi 5's 5" panel | Four screens built and wired to the state machine; PC preview with a simulated bar; kiosk install scripted. **Never run on a Pi.** [[app]], [[pi-setup]] |

```
python -m pytest firmware/tests score/tests app/tests -q
                                                  ->  218 passed  (123 firmware, 68 score, 27 app)
python tools/check_vault.py                       ->  17 notes, 0 broken links
python sim/verify_scene.py                        ->  16 checks, 0 failed
python app/server.py --preview                    ->  http://127.0.0.1:8080/preview
```

All 218 tests run on a PC with no Pi, no LED strip, no piano and no MIDI interface. As of
2026-09-30 the modules *are* wired together — `app/` is the main loop: SPI LED output
through the bench-tested encoder, a MIDI thread, a mic thread and the touchscreen UI — but
none of that wiring has met a Pi 5 yet. The SPI path itself is still the first bench task
([[decisions]] D-28).

## Your calls

1. **Pick a palette.** Three candidates; `python tools/preview_bar.py` renders all three
   through the real firmware path. Taste, not engineering.
2. **CAD file** for the mechanical build — you own the mechanical side. [[hardware]]
3. **`git init`?** Still undecided. Box has already fought one `.git` directory here.
4. **Sustain pedal?** MIDI ingest drops CC 64 today because the practice state machine has
   no pedal concept. Say if it should.
5. **Install Audiveris** if you want the PDF/photo input path — that code is written but
   its subprocess call has never run against a real binary.
6. **Photograph one lit key** through the diffuser once the bar exists. The simulation
   assumes σ = 0.45 × gap, which is what puts the "dots stop showing" threshold at a 7 mm
   gap; one photo fits the real coefficient. [[decisions]] Q-12.
7. **Weighted mapping, yes or no?** A diffused bar can be driven with a per-key weight
   profile instead of a fixed LED pair — 6.85 mm → 0.68 mm of apparent-centre error — but
   it changes the map format and breaks the hand/finger two-pixel split. [[simulation]],
   [[decisions]] Q-10 and Q-11.

## Three things that will bite you

**Key geometry is not uniform.** A0–B0 share two white-key widths, C8 gets a full one, and
everything between sits on a 13.7333 mm pitch. Any note→LED map built on a single uniform
pitch drifts. See [[hardware]].

**The shelf behind the keys is a capacitive touch surface**, not the plain shelf upstream
assumes. Power, volume and six touch buttons sit directly above the keys. A grounded
aluminium extrusion millimetres away will detune them. See [[hardware]].

**Written but unverified**, each because the hardware is absent: the Audiveris subprocess
call, the USB/BLE MIDI adapters (USB is now the primary input path), the audio benchmark
on real hardware — and, new on 2026-09-22, **that a Pi 5 can drive 176 WS2812s over SPI at
all**. Each is labelled UNVERIFIED in its own docstring. See [[firmware]] and [[hardware]]
§6.1a.

## Layout

```
README.md            you are here
log.md               append-only history, newest at the bottom
docs/                the note vault — start at docs/index.md

firmware/            Part 2, runs on the Pi      src/fingerlight/ config/ tests/
score/               Part 3, runs on the PC      src/fingerlight_score/ tests/
lightscore/          the Part 2 <-> Part 3 contract   schema/ examples/
hardware/            what you order, print and wire   bom/ cad/ wiring/ measurements/
sim/                 Part 4, MuJoCo scene built from hardware/cad/  scene.xml assets/ renders/
app/                 Part 5, the touchscreen app        server.py engine.py static/ deploy/ tests/
app/ui/              Part 5's original static mockups (the design record)
data/                generated 88-row note -> LED map
scoreforge/          ScoreForge, vendored, piano path only — not wired in

tools/gen_map.py     python tools/gen_map.py <octave_span_mm> [--origin centre]
tools/compile_score.py   MusicXML/.mxl -> light-score, validated by the Pi's own reader
tools/hw/            build-day tests: preflight, ws2812_spi, led_test, midi_test, mic_test, setup_pi.sh
tools/preview_bar.py renders the bar to HTML so palettes can be judged
tools/bench_audio.py audio-verification benchmark
tools/check_vault.py wikilink checker for docs/
```

The upstream clone is **not** in this repo. It lives at
`C:\Users\Kevin\dev\Piano-LED-Visualizer`, pinned at `b502ad67` (2026-08-30) — Box cannot
sync a `.git` directory and renamed the folder as a conflict when it lived here.
ScoreForge's guitar workflow, submodules, `songs/`, prior `output/` and its **entire git
history** are parked in `Box/Projects/Guitar_Sheet/ScoreForge-guitar-and-archive.zip`
(648 MB, 700 entries, CRC-verified). The vendored `scoreforge/` copy here has **no git
history** — that zip is the only copy, so it is not a redundant backup.

## Quick reference

| | |
|---|---:|
| M1, measured octave span | 164.8 mm |
| Playing width (A0 left edge – C8 right edge) | 1224.23 mm |
| White-key pitch | 23.5429 mm |
| Note pitch at the key-tail line, inside an octave | 13.7333 mm |
| Strip | 176 × WS2812B/SK6812 @ 144 LED/m |
| LED pitch | 6.9444 mm (≈2 per key) |
| Worst key-centre error, generated map | 3.45 mm, 0 mismatches (centred tape) |
| Same formula on upstream's defaults | 24 mismatches, 2-LED drift, G#6 and A6 collide |
| Upstream settings, at M1 = 164.8 | `shift = -2`, `note_offsets = [[21,-1],[51,1],[96,1],[107,-1]]` |
| Strip origin | +1.003 mm from A0's left edge (centred, 1.0 mm margin each end) |
| Diffuser gap / blur | 7 mm → σ 3.15 mm; below 7 mm the dots show through |
| Controller | Raspberry Pi 5 + 5" DSI touchscreen 800×480 |
| LED data pin | SPI0 / GPIO10 — `rpi_ws281x` cannot drive a Pi 5 |
| Inputs | USB-MIDI from the piano (primary); USB mic (backup, no-MIDI instruments) |
| Estimated BOM | 195–255 USD (Pi 5 platform), +10–15 for the mic |

## The docs

Nine notes, one entrance. Open the repo as an Obsidian vault and start at [[index]]; they
read fine as plain Markdown too. In Obsidian, add `scoreforge` to *Files & Links → Excluded
files* to keep the graph clean.

[[hardware]] · [[firmware]] · [[score]] · [[simulation]] · [[app]] · [[pi-setup]] · [[decisions]] · [[provenance]] · [[log]]
