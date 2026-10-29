---
name: build
description: Build day — the order of operations, the test after each step, and what each one proves
date: 2026-09-22
aliases: [Build, buildday, bringup, bring-up, runbook]
tags: [build, hardware]
---

# Build day

**You are building the bar today. Start here and work down.** Every step has a test
and a pass criterion; do not start a step until the previous one passes, because each
one is the assumption the next one rests on.

> **176 LEDs.** WS2812B/SK6812 at 144 LED/m, cut to **176**, spanning **1222.22 mm**.
> Buy ≥ 1.3 m and cut at the marks. The tape starts **1.003 mm right of A0's left edge**
> (centred: 1.0 mm of margin at each end) — [[hardware]] §2.1a.
>
> Data on **SPI0 MOSI = GPIO10, physical pin 19** — *not* GPIO18 — because `rpi_ws281x`
> cannot drive a Pi 5 ([[decisions]] D-28).

## The route

| If you want… | Go to |
|---|---|
| geometry, pins, power, BOM | [[hardware]] |
| why anything is the way it is | [[decisions]] |
| what the Pi will eventually run | [[firmware]] |
| what the bar should look like lit | [[simulation]] |
| the touchscreen app | [[app]] |
| flashing the Pi, the panel, the kiosk | [[pi-setup]] |
| what happened, session by session | [[log]] |

## The short route: one smoke run

Once the bar is wired and on the piano, one guided run covers §1–§4 in about five minutes
and leaves a report:

```
sudo systemctl stop fingerlight          # the app holds SPI and the MIDI port
python3 tools/hw/smoke.py                # preflight, 3 strip patterns, all 88 keys
sudo systemctl start fingerlight
```

It asks yes/no after each strip pattern, then lights every key you play through the real
map until all 88 have been heard. **Pass:** every step PASS — no dead key, no stuck key, no
out-of-range note. It also reports the velocity range, whether the sustain pedal arrived,
and the Pi-side note → LED time. Paste its closing block into [[log]]; the full result is
in `~/.fingerlight/smoke-report.json`. Then fix any off-centre keys on the touchscreen:
Settings → *Calibrate keys…* ([[app]] §9).

The numbered sections below are the same ground one step at a time, for when the smoke
run fails and you need to know where.

## 0. Before you open a box

```
python tools/hw/ws2812_spi.py --selftest      # encoder, no hardware needed
python tools/hw/preflight.py                  # the Pi itself
```

Two things to confirm while the parts are still returnable:

- the 5" DSI panel shipped the **Pi 5 22-pin FPC cable** (the 4B cable does not fit);
- which Type-B variant the PX-S1100's **USB TO HOST** socket takes.

## 1. Pi setup — no wires yet

Flashing the card and bringing up the touchscreen come first: [[pi-setup]] §1–§5. This
step is its §5.

```
bash tools/hw/setup_pi.sh            # dry run, prints what it would change
bash tools/hw/setup_pi.sh --apply
sudo reboot                          # if SPI or the buffer changed
python3 tools/hw/preflight.py        # must be all PASS
```

**Pass:** `preflight.py` reports 0 failures. It checks the board, `/dev/spidev0.0`,
the `spidev.bufsiz` kernel parameter, your group membership, every Python package the
tests import, and that `data/led_map.json` still says 176 LEDs.

## 2. Inputs, before anything glows

```
python3 tools/hw/midi_test.py ports      # the piano should appear
python3 tools/hw/midi_test.py watch      # play: notes and CC 64 should stream
python3 tools/hw/mic_test.py devices
python3 tools/hw/mic_test.py level --seconds 5
```

**Pass:** every key you press prints once, with the right note name; the mic reports a
healthy level. Do these first because they are cable-only problems, and debugging them
later — with a half-built bar in the way — is miserable.

## 3. Bench the strip — loose on the desk, not on the piano

Wire: **5 V brick → strip** (not from the Pi), **GPIO10 → 74AHCT125 → DIN**, **330 Ω** in
series at the strip input, **1000 µF** across the first injection point, grounds commoned
at one point. The Pi gets its own USB-C PD supply.

```
python3 tools/hw/led_test.py stages      # what each stage proves
python3 tools/hw/led_test.py first
python3 tools/hw/led_test.py walk
python3 tools/hw/led_test.py count
python3 tools/hw/led_test.py order
python3 tools/hw/led_test.py current --pct 25
```

Each stage prints what you should **see**. Where they disagree with reality, that is the
finding:

| Symptom | What it means |
|---|---|
| nothing lights | data never arrives — level shifter, ground, or SPI not enabled |
| first LED only, or garbage after it | timing: try `--one-pattern 1100` (shortens T1H to 625 ns) |
| red shows green | wire order is not GRB — pass `--order RGB` and note it |
| `count` blue is not the last LED | the strip is not 176 LEDs; fix the strip, not the map |
| far end dim or pink at 25 % | voltage drop — inject power at the far end |

**Pass:** all six stages match what they said to look for, and the current at 25 % white is
near the ~2.6 A the stage predicts.

## 4. Against the keyboard

Tape the strip down temporarily, aligned so **LED 0's centre sits 4.475 mm right of A0's
left edge** (tape origin 1.003 mm + half of the 6.9444 mm pitch).

```
python3 tools/hw/led_test.py map A0 C4 C8
python3 tools/hw/led_test.py sweep --ms 40
```

**Pass:** each lit pair sits over its own key, and the sweep tracks the key centres from
A0 to C8 without drifting. The design's worst-case centring error is **3.45 mm**, about
15 % of a white key — drift that grows toward one end is an alignment or an M1 problem,
not quantisation. [[hardware]] §2.1a, §4.3.

Keys that are off by a constant LED or two are fixed on the touchscreen, per key, and
saved as the default: Settings → *Calibrate keys…* ([[app]] §9).

## 5. Once it works

- Photograph **one lit key through the diffuser**, straight on, with a ruler in frame.
  That is the measurement that closes [[decisions]] Q-12 and lets the simulation's σ stop
  being an assumption.
- Note the actual current at 25 % and 100 % white — the BOM's 8 A sizing is calculated,
  not measured.
- Write down anything that surprised you in [[log]], same day.

## What is known to be unverified

`rpi_ws281x` failing on a Pi 5, and the SPI path working there, are both **reported and
not measured on this board** ([[hardware]] §6.1a). If SPI disappoints after stage 3, the
fallback order is RP1 PIO (`piolib`), then an external Pico. The encoder itself is
checked (`--selftest`), which narrows a failure to wiring, timing or the library beneath.

## See also

[[index]] · [[hardware]] · [[decisions]] · [[firmware]] · [[simulation]] · [[app]] · [[log]]
