---
name: firmware
description: Part 2, the Pi-side realtime layer — note map, finger colour, practice state machine, calibration, MIDI ingest, and the audio-verification benchmark
date: 2026-09-16
aliases: [Firmware, firmware-index, audio-verification-benchmark]
tags: [firmware]
---

# Firmware (Part 2)

**B1–B6 are all done: 121 tests pass on a PC with no Pi, no LED strip, and no piano
attached.**

```
python -m pytest firmware/tests -q
```

Code lives in `firmware/`, sitting **above** upstream Piano-LED-Visualizer's LED driver.
Upstream is a read-only reference, cloned at `C:\Users\Kevin\dev\Piano-LED-Visualizer`
(pinned `b502ad67`, 2026-08-30) — deliberately **outside** Box, because it was first
cloned into `external/` and Box renamed the folder to `external (jykevinhuang@ucla.edu)`,
its conflict marker for a `.git` directory it cannot sync. A re-clonable reference
doesn't belong in Box.

No LED driver, no Flask, no threads in `firmware/src/`. *(The driver that will sit under
it changed on 2026-09-22: `rpi_ws281x` cannot drive a Pi 5, so the bar is driven by
SPI-encoded bytes on GPIO10 — D-28. None of these modules care, which is the point.)* Every module except `midi.py`'s
backend adapters is pure arithmetic over data structures — why 121 tests run in under a
second on a PC, and why **nothing in `firmware/src/` imports anything Pi-specific**.
Deliberate, and now the one remaining gap: wiring these modules to upstream's driver,
web UI and main loop on a real Zero 2 W.

## Modules

| File | Does | State |
|---|---|---|
| `notemap.py` | MIDI note → LED pair, and the proof the fix is a settings change (D-08) | done |
| `colors.py` | hand + finger → RGB for the 2-LED pair (D-12, D-13, D-17) | done, palette undecided |

> **A diffuser changes both of those rows.** With the bar reading as a continuous stripe, [simulation](simulation.md) prototypes `note → [(led, weight)]` instead of a fixed pair, which cuts the apparent-centre error from 6.85 mm to 0.68 mm — but it cannot be expressed in upstream's settings and it breaks the two-pixel hand/finger split. Open as Q-10 and Q-11 in [decisions](decisions.md); nothing in `firmware/` has changed for it.
| `lightscore.py` | reader for the Part 3 light-score contract | done |
| `practice.py` | wait-for-it practice state machine | done |
| `calibration.py` | 3-anchor map calibration (D-18) | done |
| `midi.py` | MIDI ingest: pure decoder + BLE framing, guarded USB/BLE backends (D-06, B5) | pure layer done; adapters unverified |
| `audio.py` | score-informed audio verification (D-16, B6) | done, benchmarked, not run on real hardware |

## B1 — note map (D-08): all 88 notes, 0 mismatches

`notemap.py:1` states the point precisely: **we do not fork upstream's LED driver.**
Upstream's `get_note_position` (`lib/functions.py:528` in the pinned clone) is
transcribed **verbatim** at `notemap.py:72`, then fed the `shift` and `note_offsets`
that `tools/gen_map.py` derives analytically from M1 (164.8 mm). Read out through
`note_to_leds` (`notemap.py:91`), `verify_against_map` (`notemap.py:150`) checks the
result against `data/led_map.csv`: **all 88 notes match, 0 mismatches.** The correction
ships as settings, not a driver fork — D-08 confirmed empirically.

**Where the settings actually live:** `firmware/config/settings_overlay.json` holds the
generated `shift` and `note_offsets`, and is written by `tools/gen_map.py` — never by
hand. If M1 is ever re-measured, re-run the generator and let it rewrite that file;
editing the overlay directly puts it out of step with `data/led_map.csv`, and
`verify_against_map` is what will catch you.

Three settings variants, measured against the 88-row map:

| Settings | C8 → LED | Mismatches | Worst drift | Two notes on one LED? |
|---|---|---|---|---|
| Naive, no offsets | **176** (off a 176-LED strip) | 45 | 1 LED | — |
| Upstream defaults `[[92,2],[55,1]]` | 173 | 24 | 2 LEDs | **yes — G#6 and A6 both on LED 143** |
| **Ours, centred tape, shift −2** | 175 | **0** | **0** | no |

Both bad variants are pinned as regression tests in `firmware/tests/test_notemap.py`.
This **corrects an earlier HANDOFF §4.2 claim**: the LED-176 overrun belongs to the
no-offset formula, not to upstream's own defaults — upstream's actual defaults stay in
range but fail more quietly, by making two notes indistinguishable, which nothing would
have errored on.

## B2 — finger colour (D-12, D-13, D-17): palette is Kevin's call

`colors.py:1`. Ten classes (five fingers × two hands) need showing, and ten hues are not
reliably separable in peripheral vision — the player is looking at the music, not the
bar. So the two LEDs a key owns at 144 LED/m split the job: **left pixel = which hand**
(two classes, easy), **right pixel = which finger** (five classes, carried by hue).

Two constraints checked automatically: **no pure blue** (D-13, dimmest channel on these
emitters, dies against matte black keytops) and **black keys read dimmer** (D-17), so
their pixels get `BLACK_KEY_GAIN = 1.5` (`colors.py:83`) — **ASSUMED, not measured**;
matte black keytops are guessed at ~40% dimmer than ivory-textured white, pending
measurement on the built bar. Tests also enforce no two fingers closer than a separation
floor, and no hue washing out to white under the black-key gain.

Three candidate palettes — **this is a taste call, not a technical one**, and it is
Kevin's:

| Palette | Idea | Closest pair (separation) |
|---|---|---|
| `ramp` *(live, `PALETTE = "ramp"` at `colors.py:71`)* | warm→cool, thumb warmest; ordering is learnable | 110 |
| `distinct` | maximally separated hues, white as the middle-finger anchor | 170 |
| `anchors` | thumb and little finger loud, inner three muted | 100 |

Switch with `PALETTE = "..."`. Decide by looking at the built bar, not on screen.

## B3 — practice state machine

`lightscore.py` (reader for the Part 3 contract) + `practice.py:1`. Wait-for-it practice:
the bar lights the next event (all notes sharing one onset — a chord is one thing to
play), and the cursor advances only when every pitch in it is held. Partial chords stay
lit. A wrong note — one not in the current event — is reported as an error but is **not
fatal** and does not advance the cursor; rolling a chord is not penalised. The current
event always wins a pixel collision against the preview. Driven by the real fixtures in
`lightscore/examples/`, so the tests also prove the Part 3 contract is consumable by
Part 2.

**Preview depth is open question Q-06 and stays a parameter**: `preview: int = 1`
(`practice.py:56`), rendered at `PREVIEW_BRIGHTNESS = 0.18` (`practice.py:33`). The right
number is a UX call for real hardware, not a software decision.

## B4 — calibration mode (D-18): three anchors, two distinguishable faults

`calibration.py:1`. Light one LED, ask which key it actually sits over, three times, at
A0 / C4 / C8 (`DEFAULT_ANCHORS = (21, 60, 108)`, `calibration.py:38`). Three anchors
exist so the two things that can be wrong are **distinguishable**:

| Fault | Signature | Fix |
|---|---|---|
| **Translation** — bar glued off-centre | every anchor off by the same *distance* | one integer `shift` |
| **Scale** — M1 itself is wrong | error grows linearly across the board | re-run `gen_map.py` with a corrected span; **a `shift` cannot fix it** — it would just move the error to the other end |

`solve()` (`calibration.py:123`) fits `error_mm = a + b·position` over the anchors and
reports both `a` (translation) and `b` (fractional scale error), so a bad M1 can never be
silently papered over with a shift.

Two real limitations the tests document rather than hide:

- **Quantisation floor.** The player can only press a whole key, so a displacement under
  about half a key (~6.8 mm) is **undetectable** — don't chase a 2 mm discrepancy with
  this. (The code's own residual-significance constants are `NEGLIGIBLE_MM = 3.5` and
  `NEGLIGIBLE_SCALE = 0.0015`, i.e. ~0.15%, ~0.25 mm on the 164.8 mm octave —
  `calibration.py:42,45`.)
- **The end anchors clamp.** A0 and C8 are the physical ends of the keyboard, so a
  rightward offset has nowhere to land at C8 — the response clamps and a pure
  translation can be misread as a scale error. Use interior anchors when translation is
  what you're measuring.

## B5 — MIDI ingest (D-06): pure core done, adapters unverified

`midi.py:1`. USB MIDI is primary, BLE MIDI is secondary, the Pi is the USB host. Two
layers:

1. **A pure, hardware-free core** — `MidiDecoder` (`midi.py:81`, raw bytes → `NoteEvent`,
   with running status, note-on-velocity-0-as-note-off, and messages split across read
   boundaries) and `decode_ble_packet` (`midi.py:190`), the BLE-MIDI **packet framing**
   per **MMA/AMEI RP-052 §7/§9** (`midi.py:193`). Both 100% tested on a PC.
2. **Thin backend adapters** — `UsbMidiBackend`, `BleMidiBackend`, and a pure
   `ReplayBackend` for tests, behind one `MidiSource` protocol. Third-party imports
   (`mido`/`python-rtmidi` for USB, a BLE GATT client for BLE) are guarded, so the module
   and its pure core still run on a machine with neither installed — verified here, which
   has neither.

`MidiIngest` does USB-preferred/BLE-fallback source selection and fails over without
raising when a source disappears mid-session, releasing any pitches it still held so
`practice.py` never sees a stuck key. `practice.py` itself only wants
`note_on(pitch)`/`note_off(pitch)`; `apply_to_session()` (`midi.py:337`) is the thin
adapter that feeds it from a `NoteEvent` stream.

**The `mido` USB and `bleak` BLE adapters are UNVERIFIED** — no MIDI libraries, no
adapter, and no piano on this machine; only the BLE **framing** is tested, not a live BLE
link. Both must be exercised against the real target keyboard, the **PX-S1100**, over its
BLE GATT MIDI, before being trusted. **Sustain pedal CC 64 (`CC_SUSTAIN`, `midi.py:38`) is deliberately dropped** —
consumed correctly at the byte level but discarded — because `practice.py` has no pedal
concept; revisit if that changes.

## B6 — audio verification (D-16): benchmarked, not measured on the Pi

`audio.py:1`, `tools/bench_audio.py`, tests in `firmware/tests/test_audio.py`. This
replaces an earlier assumption ("audio verification runs in realtime on a Zero 2 W",
argued from operation count, never benchmarked) with a measured PC number and an
explicit, sourced extrapolation.

**The algorithm.** Not blind polyphonic transcription: during practice the light-score
already names the ≤10 candidate pitches expected right now (`Event.pitches`), so
`verify_frame(samples, sample_rate, candidates)` (`audio.py:185`) only answers "which of
these known candidates are sounding." Per frame:

1. Blackman-Harris-windowed rfft magnitude spectrum (`_blackman_harris`, `audio.py:162`)
   — not Hann, whose −31 dB sidelobes leak enough energy 50–100 Hz away to false-trigger
   an unrelated candidate against a low noise floor; a test failed under Hann and forced
   the switch.
2. Noise floor = median magnitude of the frame (robust to a few strong partials).
3. Candidates evaluated **lowest pitch first**, each needing real energy at its own
   fundamental (an SNR-relative gate, so a decayed-but-still-sounding note keeps
   reporting without a separate sustain mechanism) **and** corroboration from a fraction
   of its own harmonics.
4. A confirmed candidate's *predicted* harmonic contribution
   (`fundamental_amplitude / n`) is subtracted from the residual before the next, higher
   candidate is evaluated — this tells a real octave doubling (C3 *and* C4 both struck)
   apart from an octave ghost (only C3 struck, C4's fundamental = C3's 2nd harmonic). A
   naive "zero it to the noise floor" subtraction was tried first and produced false
   negatives on ordinary triads.
5. Piano inharmonicity is a search-band tolerance in **cents that widens with harmonic
   number**, not a fitted stiffness coefficient `B` — deliberately, since no measurement
   of the real instrument's inharmonicity exists yet.

**Tests (run, not assumed).** `python -m pytest firmware/tests/test_audio.py -q -s`: 26
passed, out of the 121-test firmware suite, including a battery sweep over **18 cases →
TP=44, FP=0, FN=0**, plus two negative controls (a lone C5 does not imply C4; a real
C3+C4 chord is not swallowed) and two tests that deliberately weaken the config to prove
the checks aren't vacuous — reintroducing the exact failure the default config exists to
prevent. **Measured limitation:** adjacent semitones are not separable below ~8192-sample
frames at 22050 Hz (~371 ms); they are not separable at 2048 samples (~93 ms) — a
frequency-resolution/latency trade-off, not a logic bug.

**Measured: PC numbers.** `python tools/bench_audio.py`, this PC (12th Gen Intel Core
i5-12400F, numpy 1.26.4, Windows 11), swept over sample rate {16000, 22050, 44100} Hz ×
frame size {2048, 4096, 8192} samples × candidate count {1, 3, 5, 10} — a 36-point sweep,
worst case (every candidate actually sounding, no early exit):

| Point | Time/frame | Realtime factor |
|---|---|---|
| Representative (22050 Hz, 4096 samples ≈186 ms, 5 candidates) | 0.383 ms | 0.0021 (~0.2% of the frame's own duration) |
| Worst swept (44100 Hz, 2048-sample frames ≈46.44 ms, 10 candidates) | 0.577–0.608 ms | **0.0124–0.0131** |

**The benchmark is not stable to three significant figures, so do not quote it that way.**
Four runs of the worst configuration on the same machine gave 0.577, 0.582, 0.593 and
0.608 ms per frame — a ±5% spread from ordinary scheduling noise. Earlier notes quoted
0.0131 and 0.0124 as if they disagreed; they are simply two runs of the same measurement.

Take the conservative end. At 0.0131 the Pi would have to be **~76× slower than this PC
before realtime verification fails** (at the fast end, ~80×). Either way there is 3–5× of
margin over the 15–25× extrapolation below — which is the point: **the conclusion does not
depend on the extrapolation being right.** Quote the ~76×, not the milliseconds.

**Extrapolated: Zero 2 W estimate — NOT measured on the actual hardware.**
`tools/bench_audio.py` applies a stated **15× (optimistic) to 25× (pessimistic)** scaling
factor, read off published Geekbench 5 single-core scores for the two chip classes
(Cortex-A53 @ ~1 GHz: ~85–95; this PC's 12th-gen Core i5 class: ~1750–1850) — a per-core
throughput ratio, not clock speed, because IPC and NEON-vs-AVX2 vector width matter more
for FFT/array-scan-heavy code. At the representative point: **5.7–9.6 ms/frame**, Zero 2
W realtime factor **0.031–0.052**. Worst swept configuration, pessimistic bound:
realtime factor **0.31** — still comfortably under 1.0.

**Caveats — why this is an extrapolation, not a fact:**

1. The 15×–25× factor compares chip *classes* from published benchmarks, not this
   project's actual Pi.
2. Assumes numpy's rfft and elementwise ops scale with general single-core throughput; a
   Raspberry Pi OS numpy build's pocketfft could scale differently.
3. Ignores thermal throttling, OS/audio-driver scheduling jitter, and contention from
   whatever else runs on the Pi at once (LED driver, MIDI ingest, practice loop).
4. Says nothing about capture-side latency (audio buffer sizes, USB audio driver
   overhead) — only the analysis function's own CPU time.

**What would settle it for real:** run `python tools/bench_audio.py` on the actual
Zero 2 W and compare its printed CPU/numpy header line against the PC numbers above. If
the measured realtime factor at the representative point comes in under roughly 0.5
(headroom for the LED driver, MIDI ingest and practice loop sharing the same core), B6 is
confirmed rather than assumed. That one run is the piece of evidence this note cannot
manufacture from a PC.

## Still unverified / needs hardware

- **Pi integration.** Nothing in `firmware/src/` imports anything Pi-specific — wire
  these modules to an LED driver, a main loop and the web app on a real **Pi 5**. The
  largest remaining chunk of work. *(The driver is no longer upstream's: `rpi_ws281x`
  does not support the Pi 5, so this is SPI-encoded bytes on GPIO10 — D-28, and itself
  unverified.)*
- **B5's `mido` USB adapter is now the primary input path** (D-27: the piano connects
  straight into a Pi 5 USB-A port) and is still untested against a real piano. The
  `bleak` BLE adapter drops to optional; only its packet framing is tested.
- **B6 on real hardware** — the Zero 2 W numbers above are extrapolated and now moot for
  this build; a Pi 5 has the headroom. Re-run the benchmark there anyway, because the
  *number* is what tells us the audio path is safe, and the mic is a different input from
  the line-in the benchmark assumed (D-29).
- **`BLACK_KEY_GAIN = 1.5`** (B2) — an assumption; measure on the built bar and correct
  the one constant.
- **The B2 palette choice** — a taste call for Kevin, decided by eye on the built bar,
  not in code.

See [hardware](hardware.md) for the build side, [score](score.md) for the Part 3 contract this consumes,
[decisions](decisions.md) for D-06/D-08/D-12/D-13/D-16/D-17/D-18, and [log](../log.md) for session history.
[index](index.md) · [provenance](provenance.md)
