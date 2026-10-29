---
name: hardware
description: Key geometry, LED strip selection, finger-colour encoding, GPIO/power wiring and the full BOM for the PX-S1100 finger-cue LED bar
date: 2026-09-16
aliases: [Hardware, 01-mechanical-and-hardware-survey, mechanical-and-hardware-survey, 00-measurements, measurements, 07-mechanical-dimension-spec, mechanical-dimension-spec, hardware-index]
tags: [hardware]
---

# Hardware

A bar of addressable LEDs behind the keys of a Casio **PX-S1100** (88-key, Smart Scaled
Hammer Action) shows which key to press and which finger to use (colour = finger), driven
from a score, with a MIDI + audio-fallback return path reporting what was actually
played. Mechanical build follows Piano-LED-Visualizer (`onlaj/Piano-LED-Visualizer`, MIT,
Python/RPi): aluminium or silicone LED profile taped behind the keys, 3D-printed Pi case.

**Scope note (2026-09-15):** mechanical design (mount, CAD) is now Kevin's. Software
tracks exactly one mechanical parameter, **M1** (octave span, §2). §3's mount analysis is
kept as reference (it drove the silicone-profile BOM choice) but no longer gates software.

Confidence tags: **CONFIRMED** (spec or direct measurement), **DERIVED** (computed from a
near-universal industry standard), **ASSUMED** (plausible, unverified).

## 1. Cabinet (CONFIRMED, Casio spec)

| Property | Value |
|---|---|
| Width × Depth × Height (no music stand) | 1322 × 232 × 102 mm |
| Weight | 11.2 kg |
| Keys | 88, Smart Scaled Hammer Action |
| USB | Type A (for WU-BT10) + Type B (USB-MIDI device) |
| Bluetooth | 5.0, A2DP audio + GATT MIDI (WU-BT10 bundled) |
| Line out | L/MONO + R, 1/4" |
| Power | 12 V DC (AD-A12150LW), 10 W |

## 2. Key geometry

### 2.1 Octave span — measured, superseding the design-stage assumption

The mechanical survey (2026-09-15) started from a DERIVED industry-standard octave span
of **164.1 mm** (7 white keys, C→B; makers vary 163–165 mm; every 88-key instrument since
~1880 follows the same standard) since no measurement existed yet. The planned method was
C1 left edge → C8 left edge ÷ 7 (7 octaves divides reading error by 7). That figure drove
the LED-density and mapping-error arithmetic in §4 and is kept there for the worked
reasoning — but it is **superseded** by a same-day direct measurement over 5 octaves.

**M1 (current, authoritative): 164.8 mm per octave.** Measured once, 2026-09-15: 5
octaves = 82.4 cm ÷ 5. Dividing by 5 cuts reading error 5×, but it's still one reading —
worth repeating once before the bar is physically aligned. Treated as **measured-once**,
not confirmed.

> **Unit-correction note.** The field reading was recorded as "82.4 mm → one octave is
> around 16.5". As millimetres, the 88-key board would be **122 mm** wide — against a
> 1322 mm cabinet, impossible. As **centimetres**, it gives 164.8 mm/octave and a
> 1224.2 mm playing width, which fits. The centimetre reading is used for M1.

Re-running `python tools/gen_map.py 164.8 --origin centre` (supersedes both the 164.1 mm
run in §4.3 and the min-error origin used until 2026-09-21, see §2.1a):

```
octave span      164.8 mm -> white pitch 23.5429 mm
playing width    1224.23 mm
strip            176 LED @ 144/m, pitch 6.9444 mm, span 1222.21 mm
strip origin     +1.003 mm relative to A0 left edge (centred on the playing width)
strip margins    +1.00 mm left / +1.01 mm right
worst centre err 3.45 mm  (15% of a white key)
unique primary LEDs: 88 / 88
PLV shift = -2, note_offsets = [[21, -1], [51, 1], [96, 1], [107, -1]]
```

All 88 notes still get a distinct primary LED.

### 2.1a Why the tape is centred, and what it cost

`gen_map.py` originally chose the origin that minimises worst-case centring error, which
put the tape **7.75 mm left of A0** and left **9.76 mm bare at the top end** — visibly
off-centre once the bar was rendered against the instrument ([simulation](simulation.md)). Centring it
instead costs **0.05 mm** of worst-case error (3.40 → 3.45 mm) and keeps 88/88 unique
LEDs, so the tape is now centred: `--origin centre`, +1.003 mm from A0's left edge, 1.0 mm
of margin at each end. 176 LEDs is the most that fit; 177 would overhang by 2.95 mm.

Two facts worth keeping visible. The error pattern is **periodic in the LED pitch**, so
sliding the tape a whole 6.9444 mm only renumbers LEDs and is absorbed by `shift` — the
0.05 mm above is the cost of the *sub-pitch* part of the move. And the regeneration
exposed a sign bug in `gen_map.py`'s PLV translation (`shift = -d(21)`, it returned
`+d(21)`), invisible for every map the tool had produced before because they all had
`shift = 0`; the firmware's own range assertion caught it — [log](../log.md) 2026-09-21.

To regenerate from a better measurement: `python tools/gen_map.py <M1>`, then update
`octave_span_mm`, `shift` and `note_offsets` in `firmware/config/settings_overlay.json`.
`firmware/tests/test_notemap.py` fails if that overlay and `data/led_map.csv` disagree,
so the two cannot silently drift.

### 2.2 Geometry used in the LED-selection reasoning (§4) — at the original 164.1 mm assumption

These numbers predate the M1 measurement and are what §4's density/error arguments were
computed against; the conclusions (144 LEDs/m, ≈2 LEDs/key, ~3.4 mm quantisation floor)
hold at either span — see the sensitivity table in §2.3.

| Quantity | Value | Notes |
|---|---:|---|
| White-key pitch | 23.443 mm | 164.1 / 7 |
| White keys on an 88 | 52 | A0 … C8 |
| Playing width (A0 left edge → C8 right edge) | 1219.0 mm | 52 × 23.443 |
| Side bezel, each end | ≈51.5 mm | (1322 − 1219) / 2 |
| Mean note pitch over the whole board | 13.85 mm | 1219.0 / 88 |
| **Note pitch inside any octave** | **13.675 mm** | 164.1 / 12 — the one that matters for LED pitch |
| Black-key width (top) | 9.5–11 mm | ASSUMED |
| Black-key rise above white surface | 10–12.5 mm | ASSUMED — sets bar height |

**Why two different pitches matter:** A0–A#0–B0 are three notes sharing two white-key
widths (15.63 mm each), and C8 gets a full 23.44 mm to itself. Between those edges, all
88 key *tails* — where a strip behind the keys sits — sit side by side at a uniform
13.675 mm. Any mapping assuming one uniform pitch across the whole board accumulates
error (quantified in §4.2).

### 2.3 Sensitivity to the measured octave span

The quantisation-error floor stays roughly constant across plausible spans, but the
offset table changes — hence "measure M1 first, then generate":

| Octave span (mm) | White pitch (mm) | Playing width (mm) | Worst err (mm) | PLV shift | PLV note_offsets |
|---:|---:|---:|---:|---:|---|
| 163.0 | 23.286 | 1210.9 | 3.41 | −1 | `[[40,1],[63,1],[86,1]]` |
| 163.5 | 23.357 | 1214.6 | 3.43 | −1 | `[[40,1],[66,1],[92,1],[107,−1]]` |
| 164.0 | 23.429 | 1218.3 | 3.39 | 0 | `[[49,1],[80,1]]` |
| 164.1 (old assumption) | 23.443 | 1219.0 | 3.42 | −1 | `[[47,1],[79,1]]` |
| **164.8 (measured M1)** | **23.5429** | **1224.23** | **3.4** | **0** | **`[[63,1]]`** |
| 164.5 | 23.500 | 1222.0 | 3.42 | 2 | `[[58,1],[96,1],[107,−1]]` |
| 165.0 | 23.571 | 1225.7 | 3.41 | 0 | `[[70,1]]` |

A **calibration mode** should ship in firmware regardless: light LED *n* alone, step with
the encoder, confirm it lands on the expected key at A0, C4 and C8. Three confirmations
pin the map, beating any spec sheet.

## 3. Mounting (reference — mechanical is now Kevin's, §1)

PLV's method, unchanged in principle: aluminium LED profile with frosted diffuser, cut
to length, double-sided taped to the piano immediately behind the keys, Pi in a
3D-printed case (`Docs/RPICaseModel.stl`) stuck to the cabinet near one end, M2.5 × 10 mm
nylon standoffs, braided cable run, optional reed/mechanical switch on **BCM 12** for
key-cover detection (skipped here — PX-S1100 has no key lid). PLV's alternative to the
aluminium channel is a silicone profile (AliExpress **T0515**, 12 mm, 2 m).

**PX-S1100-specific problem:** unlike the flat shelf PLV was developed against, the panel
behind the keys on the PX-S1100 *is* the control surface — power button, volume knob and
six capacitive touch buttons (FUNCTION, SOUND MODE, METRONOME, demo/library, REC, GRAND
PIANO) sit in a row directly above the keys. A profile taped flat across the full 1.23 m
width covers them, and a grounded metal extrusion within a few millimetres of a
capacitive button will detune or falsely trigger it — hold aluminium ≥ 10 mm clear, or
use the silicone T0515 profile and stop worrying about it (preferred; drove BOM item 2).

Three mount options, picked by clear panel depth (M3):

| Option | Pick it when | Trade-off |
|---|---|---|
| A. Taped flat in the front band (PLV-standard) | M3 ≥ 12 mm clear panel | lowest profile, no printed parts; LEDs graze keys, black keys cast shadows |
| B. Printed low wedge, profile tilted 15–20° forward | M3 ≥ 12 mm, want light *on* keytops | +8–10 mm height, two printed end wedges |
| C. Printed standoff rail bridging the touch row | M3 too shallow | best optics, keeps every button reachable; needs a 1.2 m rail or three spaced feet, must clear the music-stand slot |

**Measured against the CAD in [simulation](simulation.md) (2026-09-21):** the top board's front rim is 21.32 mm *in front of* the key tails — the keys run under it — so the bar is anchored on that rim, not on the tails. Laid flat there the bare tape tops out 0.67 mm *below* the black keys (option A, with its shadow problem); the diffuser channel puts the lit surface 7.83 mm above them. Clearance under the bar is +9.85 mm.

Target bar height: **10–15 mm above the black-key tops** so a lit black key isn't
occluded by neighbours.

### Measurement worksheet — current status

| # | Measure | Expected/value | Status |
|---|---|---|---|
| **M1** | Octave span (C1→C8 ÷ 7, or 5 octaves ÷ 5) | **164.8 mm** | **measured**, 2026-09-15 (§2.1) |
| M2 | A0 left edge → C8 right edge | 1219 mm (at old assumption) | dropped — mechanical out of software scope |
| **M3** | Clear panel depth (key tails → touch-button row) | **≈150 mm** | recorded, not tracked — comfortably over the 12 mm option-A threshold, so nothing about the LED bar is depth-constrained. See [decisions](decisions.md) D-09 for the capacitive-touch constraint that motivated it |
| M4 | Panel height above white keytop surface | — | dropped |
| M5 | Black-key top height above white-key top | 10–12.5 mm | dropped |
| M6 | Touch-button row horizontal extent + end gaps | — | dropped |

## 4. LED selection

### 4.1 Density — arithmetic, not preference

| Density | LED pitch | LEDs per note (13.675 mm) | Verdict |
|---|---:|---:|---|
| 30/m | 33.3 mm | 0.41 | one LED per 2.5 keys — useless |
| 60/m | 16.7 mm | 0.82 | fewer LEDs than keys — cannot address a key |
| 96/m | 10.4 mm | 1.31 | non-integer, keys share LEDs |
| **144/m** | **6.944 mm** | **1.97** | **≈2 LEDs per key; 176 LEDs ≈ exact playing width** |
| 240/m (rare, 3535 pkg) | 4.17 mm | 3.28 | more resolution, 1.7× the current, poor availability |

176 LEDs at 6.944 mm spans 1222.2 mm against a 1219.0 mm playing width (at the old 164.1
mm figure) — a 3.2 mm mismatch over 1.2 m, about ⅛ of a white key. 144/m is also the
practical ceiling for WS2812B; above it you're into 3535-package minis with worse
availability and thermal headroom.

**Selected: 176 × WS2812B (or SK6812) at 144 LEDs/m, 5 V, black or white flex PCB, IP30**
(bare — pair with a proper diffuser, not silicone potting).

- **WS2812B** — native to Piano-LED-Visualizer's `rpi_ws281x` driver (WS281x-only).
  Default choice.
- **SK6812** — pin/protocol-compatible, better colour consistency, lower minimum current
  per channel (matters for the dim-hint colours in §5). Drops into the same driver.
  Worth the small premium.
- **APA102/SK9822** — 2-wire SPI, no timing criticality. Nicest electrically, but not
  supported by the software base and 144/m parts are scarce. Rejected on integration
  cost, not merit.
- Avoid RGBW (SK6812-RGBW): the extra white die eats a quarter of the pitch budget and
  isn't needed.

### 4.2 Mapping problem

Piano-LED-Visualizer computes the LED for a note at `lib/functions.py:528`:

```python
density      = leds_per_meter / 72          # = 2 for a 144/m strip
note_pos_raw = int(density * (note - 20) - note_offset)
```

That's a uniform **13.889 mm/note**. The real tail pitch inside an octave is 13.675 mm
— a 0.214 mm/note difference that accumulates:

| Mapping | Best-case worst error | As fraction of a white key |
|---|---:|---|
| Stock uniform `2·(note−20)` | 8.89 mm | 38% |
| Geometric LUT (this design) | 3.42 mm | 15% — 3.47 mm is the LED-pitch-÷-2 quantisation floor |

The stock formula also overruns: it asks for LED index **176** on a 176-LED strip (valid
indices 0–175).

Fix: the stock code already has a user-editable piecewise `note_offsets` table (meant to
be hand-nudged in the web UI). `tools/gen_map.py` generates that table analytically from
the measured octave span instead — building the tail-line geometry, picking the strip
origin that minimises worst-case error, and emitting both a full per-note LUT
(`led_map.csv` / `led_map.json`: tail centre, tail width, primary LED, LEDs inside each
key, residual error) and the equivalent PLV settings. At the 164.1 mm design figure this
yields a strip origin of **-13.75 mm** relative to A0's left edge (strip starts ~14 mm
left of A0), 84 keys with 2 LEDs, 1 key with 3, 3 keys with 1, and 88/88 unique primary
LEDs (current, measured-M1 output is in §2.1).

### 4.3 Diffusion and optics

Bare 144/m WS2812B behind a piano is a row of painful point sources. Required:

- **Aluminium channel + frosted PC/PMMA diffuser**, ~1.25 m, 8–12 mm internal width
  ("LED profile 1710"/"MINI-A" class), **or** the silicone **T0515** 12 mm × 2 m profile,
  cut to 1230 mm. Silicone preferred on this instrument (§3, capacitive touch row).
- Diffuser–LED gap of 6–10 mm gives clean per-key blobs at 6.9 mm pitch without smearing
  adjacent keys. Under ~4 mm still shows dots; over ~12 mm bleeds into neighbours.
  **Now derived, not cited** ([simulation](simulation.md)): driving every LED equally and measuring how much the summed field undulates gives 69% ripple at a 4 mm gap, 39% at 5 mm, 12% at 6 mm and 0.0% at 7 mm. The lit width therefore cannot be narrowed by lowering the cap — below ~7 mm it stops being a stripe.
- **Black keys read ~40% dimmer** than white under the same LED (matte black vs.
  ivory-textured white). Firmware needs a per-key-type gain, budget **1.4–1.7×** on
  black keys (implemented as `BLACK_KEY_GAIN` in `firmware/src/fingerlight/colors.py` —
  still an ASSUMED value, needs measuring on the built bar).

### 4.4 Power

| Item | Figure |
|---|---:|
| WS2812B, full-white, per LED | ~60 mA @ 5 V |
| 176 LEDs, theoretical max | 10.6 A / 53 W |
| Realistic learning mode (≤24 keys lit, 45% brightness) | < 1.0 A |
| Full backlight effects at 50% | ~5 A |
| Raspberry Pi 5, peak | 5 A (its own USB-C PD supply; do not share the strip's rail) |
| ~~Raspberry Pi Zero 2 W, peak~~ | ~~0.6 A~~ *(superseded, D-27)* |

- Supply: **5 V / 8 A (40 W)** mean-well-class brick with a firmware global-brightness
  cap; 5 V/10 A if unrestricted backlight modes are wanted. **The Pi 5 gets its own 5 V/5 A
  USB-C PD supply** — it is not fed from the LED brick — with grounds commoned at one
  point. (D-27; a Pi 5 that browns out mid-passage is indistinguishable from a firmware
  bug, which is exactly the debugging session to avoid.)
- **Inject power at three points** — both ends and the centre (LED 88); at 144/m the
  flex PCB copper is thin and a single end feed browns the far end at high duty.
- **1000 µF** electrolytic across the first injection point; **330 Ω** in series on the
  data line at the strip input; common ground between PSU, Pi and strip.
- Do not power the strip from the Pi's 5 V rail, and do not power the Pi from the strip's
  far end.

### 4.5 Level shifting

Pi GPIO is 3.3 V; WS2812B wants V<sub>IH</sub> ≈ 0.7 × V<sub>DD</sub> = 3.5 V at a 5 V
rail. It often "works" and then fails once the strip warms up.

- **74AHCT125** (quad buffer, 5 V rail, TTL inputs) on the data line — $0.50, ends the
  problem. Alternative: 74HCT245.
- The one-diode trick (drops the strip to ~4.4 V) also works and costs less, but loses
  brightness headroom.

## 5. Finger colour encoding

10 classes to encode (5 fingers × 2 hands) on ~2 LEDs/key, readable at a glance through a
diffuser, on both key colours. 10 distinct hues don't work reliably in peripheral vision,
so each key's two adjacent LEDs split the job:

```
key = [ LED_a ][ LED_b ]
        ^hand    ^finger
```

- **LED_a** (left pixel): hand — cool white-blue = left, warm amber-white = right.
  Low-information, high-salience confirmation cue (hand is usually obvious from register
  anyway).
- **LED_b** (right pixel): finger, 5-hue palette chosen for maximum separation:

| Finger | Colour | RGB | Why |
|---|---|---|---|
| 1 thumb | red | 255, 0, 0 | maximum salience for the anchor finger |
| 2 index | amber | 255, 110, 0 | |
| 3 middle | green | 0, 255, 0 | |
| 4 ring | cyan | 0, 180, 255 | |
| 5 little | violet | 190, 0, 255 | |

Deliberately no pure blue — dimmest channel per unit drive, dies on matte black keytops.

The 3 keys that are 1-LED-wide (§4.2) fall back to finger-only; the 1 key that gets 3
LEDs shows hand-finger-hand. Three palette candidates exist in firmware (a taste call,
pick by looking at the bar) — see [firmware](firmware.md).

This is new work: Piano-LED-Visualizer only colours by hand (`hand_colorR`/`hand_colorL`
in `lib/learnmidi.py`), never by finger, and carries no fingering data.

## 6. Controller and peripherals (Raspberry Pi 5)

**Platform changed 2026-09-22 (D-27):** Raspberry Pi 5 + a 5" DSI capacitive touchscreen
(800×480) + a USB microphone, replacing the Zero 2 W, the SPI LCD HAT and the OTG hub.
§6.1a is the consequence that matters; §6.1 below is kept because its analysis is still
the reason the LED pin is where it is.

### 6.1a The Pi 5 cannot drive WS2812 the stock way — LEDs move to SPI0/GPIO10 (D-28)

`rpi_ws281x` reaches the LEDs by DMA-ing into the BCM2835's PWM/PCM/SPI peripherals. The
Pi 5 puts GPIO behind the **RP1** southbridge and that library does not support it. Three
paths exist; only one needs no parts we were not already buying:

| Path | Extra hardware | Notes |
|---|---|---|
| **SPI-encoded bytes on GPIO10 (chosen)** | none beyond the 74AHCT125 already in the BOM (§4.5) | e.g. `adafruit-circuitpython-neopixel-spi`: WS2812 bits become SPI bytes at ~2.4 MHz, no DMA tricks. 176 LEDs ≈ **1.6 kB per frame**, inside the stock 4096-byte `spidev` limit (§6.1 option A did the arithmetic) |
| RP1 PIO (`piolib`) | none | Raspberry Pi's own WS2812 example, native to the Pi 5, but newer and less travelled |
| Pico/QT Py over USB | +1 board | bulletproof timing, one more firmware to maintain |

D-10's own reversal clause called this shot: *"dropping the LCD HAT and the hub → move
LEDs to SPI0/GPIO10"*. The DSI panel drops the LCD HAT, which frees SPI0, and the Pi 5
forces the move anyway. Two caveats from §6.1 option A **fall away on a Pi 5**: `core_freq`
no longer gates the SPI clock, and the I²S-vs-PWM conflict is moot because audio now comes
in over USB.

> **UNVERIFIED — first bench task when the Pi 5 arrives.** That `rpi_ws281x` fails on a Pi 5
> and that the SPI path works there are both *reported*, not measured on Kevin's board. Run
> 176 LEDs, sweep the bar, watch for flicker and for the last LED lagging. If SPI
> disappoints, the fallback order is PIO, then a Pico.

### 6.1 GPIO conflict: LED PWM vs. I²S audio capture *(Zero 2 W era, superseded by §6.1a)*

`rpi_ws281x` drives WS281x by DMA-ing into a PWM FIFO, normally on **GPIO18** — which is
also **PCM_CLK** (GPIO19 = PCM_FS), so the stock LED pin and an I²S audio input cannot
coexist:

| Option | LED pin | Audio in | Cost |
|---|---|---|---|
| **B (recommended, stock PLV wiring)** | **GPIO18** PWM0 | USB audio interface | costs one port on the OTG hub already required; keeps the Waveshare LCD HAT on SPI0, wiring diagram unchanged |
| A | SPI0 MOSI, GPIO10 (`rpi_ws281x` SPI mode) | I²S on GPIO18/19/20 + PCM1808 ADC | needs `core_freq=250` and `spidev.bufsiz=32768`; strip becomes the only SPI0 device, LCD HAT must be replaced by an I²C OLED. Upside: SPI only needs the `gpio` group, daemon stops needing root |
| C | GPIO13 PWM1 | I²S | needs `led_channel=1`; frees SPI0 for the LCD but still collides with PCM_CLK's neighbours on some HATs |

**Took B** on the Zero 2 W. On the Pi 5 this is settled differently — see §6.1a.
A USB audio input is one more port on a hub already needed for the piano;
every PLV wiring instruction and photo stays valid. PWM mode requires blacklisting
`snd_bcm2835` (onboard audio out) — irrelevant here since audio *in* is over USB.

Option A kept in reserve for a later hub-free revision: 176 LEDs need
176 × 24 × 3 ÷ 8 ≈ **1.6 kB** per SPI frame, inside the stock 4096-byte limit — but set
`spidev.bufsiz` anyway so a longer strip doesn't fail mysteriously.

### 6.2 Getting MIDI in

PX-S1100's USB TO HOST socket is a **device** port, so the Pi is the host. On the Pi 5
that is one cable into one of four USB-A ports — no OTG adapter, no powered hub, and the
microphone gets its own port. (Check which Type-B variant your PX-S1100 uses before buying
the cable.) The Zero 2 W analysis below assumed a single OTG port:

| Path | Latency | Notes |
|---|---:|---|
| **USB-MIDI host** (Pi 5 USB-A → the piano's USB TO HOST) | < 2 ms | **the** input path (D-27). Class-compliant, no adapter, no hub |
| BLE MIDI (bundled WU-BT10) | 10–30 ms, jittery | optional convenience path, never the practice-timing path — UI should say which is active |
| **USB microphone** | 20–60 ms + detection | **backup input for instruments with no MIDI out** (D-29), and a cross-check when MIDI is present. Not the timing reference |

### 6.3 Compute limits — what the Pi 5 changes

*(The Zero 2 W analysis is kept below; it is why the score compiler is a separate PC-side
process at all.)*

The Pi 5 (4×A76 @ 2.4 GHz, 4–8 GB) removes both of the Zero 2 W's hard limits, which
**re-opens two decisions rather than settling them**:

- **OMR on the Pi becomes possible.** Audiveris wants a JVM and 2 GB+ heap, which an 8 GB
  Pi 5 has. That is precisely the reversal condition D-23 records for "score compiler stays
  a PC-side process". Possible is not desirable — a JVM on the practice machine is a lot of
  moving parts for an import step nobody runs mid-session — so this stays **open**, noted,
  not acted on.
- **Score-informed audio verification stops being tight.** The benchmark that had ~76×
  headroom on a *PC* and was only extrapolated to a Zero 2 W ([firmware](firmware.md) §B6) is no longer
  a risk item on this platform.

**Zero 2 W (historical):** 4×A53 @ 1 GHz, 512 MB. Two workloads did not fit:

- **OMR (PDF/photo → score).** Audiveris (JVM) wants 2 GB+ heap on a full page — will
  not run on a Zero 2 W. Must be an offline import step on a PC/container producing a
  compiled score file pushed to the Pi. Clean split: score compilation isn't realtime.
- **Blind polyphonic transcription** (Onsets-and-Frames / basic-pitch class models). Also
  not realtime on a Zero 2 W.

### 6.4 The audio path is easier than it looks

During practice the system already knows which notes are expected, so the problem
collapses from "transcribe a piano" to **score-informed verification**: for the ≤10
candidate pitches at the current position, run a small bank of tuned resonators / a
constrained NMF against a fixed per-note spectral template and decide present/absent —
a few hundred multiply-accumulates per hop, comfortable on a Zero 2 W. It's a
verification/onset-timing channel, not transcription — and it lets the bar work on an
acoustic piano too.

## 7. Bill of materials (single unit, USD, indicative)

PLV's own list (PLV quotes $75–100 for the base build), with PX-S1100 deltas marked ▲.
~$120–150 of the $125–155 base build is settled and orderable now; only the mount
wedge/rail item is blocked (now resolved — mechanical is Kevin's, §1/§3).

| # | Item | Qty | Est. USD | Note |
|---|---|---:|---:|---|
| 1 | WS2812B/SK6812 144 LED/m strip, ≥1.5 m, IP30 | 1 | 25–40 | PLV-specified |
| 2 | **T0515 silicone profile 12 mm, 2 m** (or aluminium channel + diffuser) | 1 | 10–20 | ▲ silicone preferred — capacitive touch row, §3 |
| 3 | **Raspberry Pi 5** (4 GB min, 8 GB if OMR on-device is ever wanted) | 1 | 60–80 | ▲ D-27, supersedes the Zero 2 WH; forces the LED driver change in §6.1a |
| 3a | **Pi 5 USB-C PD supply, 5 V/5 A (27 W)** | 1 | 12–15 | ▲ separate from the LED brick (row 5) |
| 3b | Active cooler / heatsink for the Pi 5 | 1 | 5–10 | ▲ a throttling Pi mid-passage looks like a firmware bug |
| 4 | microSD 32 GB Class 10 / A1 | 1 | 8 | |
| 5 | 5 V **8 A** PSU + 5.5×2.5 mm socket w/ quick connect | 1 | 20–28 | ▲ PLV says 6 A @ 50%; 8 A gives headroom |
| 6 | 22–18 AWG wire, ~1 m; heat shrink; cable braid | — | 8 | PLV-specified |
| 7 | **74AHCT125** + 1000 µF + 330 Ω + protoboard | 1 | 5 | ▲ PLV wires GPIO18 straight to DIN; add the buffer |
| 8 | USB-A → piano USB TO HOST cable | 1 | 5 | ▲ the Pi 5's four ports remove the OTG hub entirely (D-27) |
| 9 | **5" DSI capacitive touchscreen, 800×480** | 1 | 35–50 | ▲ D-27, replaces the LCD HAT — confirm it ships the **Pi 5 22-pin FPC cable**, the 4B cable does not fit |
| 9a | Screen mount / stand | 1 | 5–15 | ▲ on the instrument or beside it — mechanical, Kevin's side |
| 10 | 3D-printed Pi case (`Docs/RPICaseModel.stl`) + M2.5×10 standoffs & screws | 1 | 6 | PLV-specified |
| 11 | Double-sided tape | — | 3 | PLV-specified |
| 12 | Reed/mech switch on BCM 12 (cover detect) | 1 | 2 | PLV option — **skip**, PX-S1100 has no key lid |
| 13 | **USB microphone** (driver-free, lavalier/buttonhole class) | 1 | 8–15 | ▲ D-29 — backup input for instruments with no MIDI out; replaces the UCA202 line-in plan |
| 14 | Printed mount wedge / standoff rail | — | 5 | ▲ only if mount option B or C is chosen |
| | **Total, base (Pi 5 platform)** | | **≈195–255** | ▲ re-costed 2026-09-22; the Zero 2 W build was ≈125–155 |
| | **+ mic backup path** | | **≈205–270** | |

Buildable artifacts live under `hardware/` at the repo root: `bom/` (orderable parts
list), `cad/` (printed mount parts — wedge for option B, standoff rail for option C),
`wiring/` (**SPI0/GPIO10** data line per §6.1a — the diagrams still say GPIO18 and need
redrawing, 74AHCT125 buffer, 3-point power injection), `measurements/`
(photos and readings off the instrument).

Optional later: a **custom LED bar PCB** with emitters at the exact tail centres from
`led_map.csv` instead of a uniform 6.944 mm strip — removes the 3.4 mm residual entirely
and allows 3 LEDs on every key. At 1.22 m it panelises as 3 boards of ~410 mm joined by
castellated edges. Worth doing only after the strip version proves the concept.

## 8. Settled vs. deltas from stock PLV

**Settled:** 176 × 144/m WS2812B/SK6812; geometric LUT instead of the stock uniform
formula; 2-LED hand+finger encoding *(under review, Q-10/Q-11)*; **Pi 5, LED data on
SPI0/GPIO10, 5" DSI touchscreen, no hub** (D-27/D-28, was: Zero 2 W, GPIO18 PWM, LCD HAT);
USB microphone as the no-MIDI backup (D-29); USB-MIDI host primary / BLE MIDI optional;
OMR as an offline compile step; T0515 silicone profile; 5 V 8 A PSU with 3-point
injection.

| # | Change from stock PLV | Why |
|---|---|---|
| D1 | Analytic `note_offsets`/`shift` instead of hand-nudged values | §4.2 — 8.9 mm → 3.4 mm |
| D2 | Per-key-type brightness gain (black keys ×1.4–1.7) | §4.3 |
| D3 | Finger colour layer: 2-LED hand+finger pair | §5 — no equivalent upstream |
| D4 | Fingering engine + MusicXML/OMR ingestion | §6.3 |
| D5 | Score-informed audio verification path | §6.4 |
| D6 | 74AHCT125 on the data line | §4.5 |
| D7 | Mount adapted to the capacitive touch row | §3 |
| D8 | Calibration mode (A0/C4/C8 confirm) | §2.3 |
| D-17 | Black-key gain 1.4–1.7× as `BLACK_KEY_GAIN` constant | §4.3 — assumption, needs measuring on the built bar |

## 9. Still open / assumed

- **Black-key gain** (D-17, §4.3): ASSUMED 1.4–1.7×, single constant in
  `firmware/src/fingerlight/colors.py`. Needs measuring on the built bar, then correcting
  in that one place.
- **Finger-colour palette** (§5): a taste call, three candidates implemented — pick by
  looking at the bar. See [firmware](firmware.md).
- **M1 re-check**: current 164.8 mm is measured-once; worth re-measuring once the bar is
  physically aligned (§2.1).
- **Black-key geometry** (§2.2): width 9.5–11 mm and rise 10–12.5 mm above white-key
  surface remain ASSUMED, not directly measured.

Entrance: [index](index.md) · related: [decisions](decisions.md) · [log](../log.md) · [firmware](firmware.md) · [score](score.md) ·
[provenance](provenance.md)
