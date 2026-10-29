---
name: app
description: Part 5 — the touchscreen app: what it must do, the four screens, how they are wired to the firmware, and how to run them
date: 2026-09-30
aliases: [App, ui, UI, gui, touchscreen]
tags: [app, part5]
---

# Part 5 — the app

A web app in kiosk-mode Chromium on the Pi 5's 5" DSI panel (D-30), the same app reachable
from a phone on the same network.

**Built and wired, 2026-09-30.** All four screens run against the real firmware state
machine: `python app/server.py --preview` on a PC, then http://127.0.0.1:8080/preview —
§7. Renders of the live app are `app/shots/shot_*.png`. The original static mockups
(`app/ui/`, `app/ui/shot_*.png`) are kept as the design record; the live app follows them
except where §8 says otherwise. Installing it on the Pi: [pi-setup](pi-setup.md).

## 1. The rule the design follows

**The bar is the display. The screen is the remote.** Every pixel that competes with the
LEDs for the player's eyes is a cost, so the screen's job while playing is: transport,
where am I, and how am I doing — nothing that needs reading mid-phrase.

## 2. What 800×480 on a 5" panel actually gives you

933 px across a 5 in diagonal is **187 ppi, so 1 mm ≈ 7.35 px**. A comfortable finger
target is ~9 mm (**66 px**, `--tap`) and the floor is ~7 mm (**52 px**, `--tap-min`).

That arithmetic decides the layouts, not taste:

- a 56 px top bar + 12 px gutters leaves **~400 px** of working height;
- that is **one row of 66 px controls plus one content block**, or **four list rows**;
- the library shows **4 scores at a time** — the fifth row is a scroll affordance, not a
  clipped row.

No CDN, no webfont, no framework: the Pi will sometimes be offline beside the piano, and a
UI that needs the network to render is a UI that fails exactly when it matters.

Finger colours in the UI are the firmware's own `PALETTE_RAMP` values, so the chip on the
screen and the LED on the bar are the same hue rather than two people's idea of "amber".

## 3. The four screens

| Screen | Answers | Render |
|---|---|---|
| **Play** | what do I press now, how far in am I, how is this attempt going | `app/ui/shot_play.png` |
| **Library** | what shall I practise | `app/ui/shot_library.png` |
| **Session** | what went wrong, and what do I do about it | `app/ui/shot_session.png` |
| **Settings** | mode, palette, brightness, look-ahead, input | `app/ui/shot_settings.png` |
| **Free play** | the bar as a light show for whatever is played (§9) | — |
| **Calibrate** | per-key LED offsets, saved as the default map (§9) | — |

**Play.** Left: the notes due now as finger-coloured chips (note name + finger, the
waiting ones outlined), with the next event dimmed underneath. Right: accuracy, progress,
the active loop, misses so far. Bottom: loop / prev / **play-pause** / next, tempo −/+, and
the practice-mode toggle. One row, all ≥ 52 px.

**Library.** Four scores per screen, each with bars / hands / last played / last score.
Hand filter and **Practise** on the action row. "Imported, not compiled" is a real state —
the score compiler is a PC-side step (D-23), so the app must be able to show a score it
cannot yet play.

**Session.** Deliberately the opposite of Play: this is the screen you read. Accuracy, note
count, hesitations, delta against the previous attempt; and a list of *where* it went wrong
with the finger colour, the bar number and the reason (hesitated / wrong key / missed). The
two actions that follow from it are on screen: **slower −10%** and **loop that bar only**.

**Settings.** Practice mode (wait-for-me vs play-along), palette (the three candidates
from B2, still undecided until the bar exists), brightness (which also caps current draw),
look-ahead depth (Q-06), and input — with **Microphone greyed out while USB-MIDI is live**,
because D-29 makes the mic a backup, not a second opinion.

## 4. Flows

```
Library --Practise--> Play --(reaches end / stop)--> Session --Try again--> Play
                        |                               |
                        +--------- Settings ------------+
```

Two things worth noticing in that diagram: **Session is on the path, not a menu item** —
the loop-and-slow-down decision is the product — and **Settings is reachable from Play**,
because brightness and look-ahead are things you change *while* playing, not before.

## 5. What the firmware can already serve, and what it cannot

| The screen needs | Exists today | Where |
|---|---|---|
| current event, its notes, hands, fingers | **yes** | `practice.PracticeSession.frame()` → `Frame.pixels`, `waiting_for` |
| progress (n of m events) | **yes** | `PracticeSession.progress()` |
| wrong notes, error count | **yes** | `session.errors`, `session.wrong_notes` (index, pitch) |
| look-ahead depth | **yes**, as a parameter | `PracticeSession.preview`, default 1 (Q-06 unresolved) |
| wait-for-me mode | **yes** — it is what the state machine does | `note_on` advances only when satisfied |
| **play-along mode** | **no** | needs a clock the session advances against, not just note events |
| **bar / beat numbers** | **no** | the light-score carries onsets in ms, not bar numbers |
| **tempo scaling** | **no** | nothing consumes a tempo factor yet |
| **loop A–B** | **no** | the session has one cursor and no range |
| **attempt history / deltas** | **no** | nothing persists between sessions |
| score library with metadata | **no** | `lightscore.load()` reads one file; there is no index |

So the Play screen is roughly *buildable today*, and **Session, the loop controls and the
tempo controls each need firmware work first**. That is the useful output of drawing them:
four screens, three of them ahead of the state machine.

**Update 2026-09-30 — how each gap was closed.** The state machine stays pure; everything
that needs a clock or a file lives in the app engine (`app/engine.py`):

| Gap | Now | Where |
|---|---|---|
| play-along mode | a score clock × tempo; an event the clock passes unfinished is recorded **missed**; a player *ahead* of the clock is never held back | `Engine.tick` |
| tempo scaling | 30–150 %, play-along only (wait mode has no clock to scale); the lead-in is one wall second at any tempo | `Engine.transport("tempo")` |
| loop A–B | one button: tap = start here, tap = end here and loop, tap = clear; Session's *loop here* = that event ±2 | `_loop_button`, `loop_spot` |
| attempt history / deltas | `~/.fingerlight/history.json`, last 50 per score; delta in **percentage points** | `Engine._finish` |
| score library | every `*.lightscore.json` in `~/.fingerlight/scores`, plus the demos and examples; sources listed as *not compiled* | `scan_library` |
| bar numbers | **still no** — the format has no time signature. The app shows **beats** (from the tempo map) and seconds instead | `beat_at` |
| cursor jumps | one firmware addition: `PracticeSession.seek(i)` (clamped; keeps the error tally, drops partial credit) | `practice.py`, tested |

## 6. Open

- **Play-along vs wait-for-me** — both are in the UI; only the second is implemented. Which
  is the default is a practice-method question for Kevin, not a technical one.
- **Bar numbers.** The UI wants them everywhere ("loop bars 9–16", "bar 11"), the format has
  milliseconds. Either the compiler emits bar/beat alongside `t`, or the app derives them
  from the tempo map — the light-score schema already carries `tempo`.
- **Q-06 look-ahead depth** is exposed as a control rather than settled, which is probably
  the honest answer until the bar is built.
- **Q-10 / Q-11** (weighted mapping, hand/finger encoding) will change what a "chip" means
  on the Play screen: one blob per key cannot carry hand and finger as two colours.

## 7. Running it

```
python app/server.py --preview            # PC: open http://127.0.0.1:8080/preview
python3 app/server.py --host 0.0.0.0      # Pi: LEDs on SPI, MIDI + mic threads
python -m pytest app/tests -q             # 45 tests, PC-only
```

Stdlib only — no Flask, nothing to install on either side. One server serves the panel,
any phone, and the preview; each gets the full state on every change over server-sent
events, so a tap on the phone shows on the panel at once.

```
app/server.py       HTTP + SSE; GET /api/state, POST /api/open|transport|note|settings|retry|
                    pedal|freeplay|calibrate
app/engine.py       attempts, library, settings, history, loops, play-along clock,
                    free play, key calibration
app/inputs.py       MIDI thread (skips ALSA's "Midi Through"), mic thread (D-29 backup)
app/leds.py         SPI sink reusing tools/hw/ws2812_spi.py, or none
app/static/         index.html + app.js + app.css (the panel); preview.html + preview.js
app/deploy/         fingerlight.service, kiosk.sh, install_app.sh -> [[pi-setup]] §7
app/demo_scores/    two compiled fixtures; regenerate with make_demo.py, never edit
tools/compile_score.py   PC: MusicXML/.mxl (or PDF via Audiveris) -> light-score
```

**How "correct" is counted.** Accuracy = notes credited ÷ (credited + wrong keys + missed).
A skipped event is listed in *where it went wrong* but is not in the denominator — skipping
is a choice, not a mistake. A hesitation (wait mode) is an event that took longer than
both 1.5 s and 2.5 × the attempt's median; both numbers are guesses to tune on the bar.

## 8. Where the live app departs from the mockups

- **Tabs are 52 px tall**, not 36 — the mockups broke this note's own 52 px floor (§2).
- **The panel's size is not assumed.** The layout is 800×480 and scales to the screen:
  1.0 on an 800×480 panel, 1.5 (letterboxed) on a Touch Display 2 turned landscape.
- **Beats, not bars** (§5). **Session rows carry a *loop here* button**, so they are 58 px
  touch rows rather than 44 px text rows.
- **Finger chips use the live palette.** Switching palette in Settings recolours the chips
  and the bar together.

Verified on the PC (Chromium over CDP, 2026-09-30): every screen at 800×480, Play also at 1280×720,
no element outside the panel, no button under 52 px, no console errors. Not verified: any
of it on the Pi, on the panel, with a finger.

## 9. Free play and key calibration (2026-10-29)

Two screens added for bring-up, both reachable on the PC preview.

**Who owns the bar.** At any moment exactly one thing draws on it (`Engine.bar_owner`):
the **Calibrate** screen while it is open, else a **live attempt**, else **free play**. A
stopped or finished attempt is no longer live, so the bar falls back to free play the
moment a score ends. (This also fixed a bug: a key pressed after *Stop* used to un-pause a
summarised wait-mode attempt and could write a second history row.)

**Free play** (tab). Every key you play lights its LED pair and fades when released.
Settings, all persisted in `settings.json` as `fp_*`:

| | |
|---|---|
| Style | *solid* (one colour) · *split* (left / right of a split key) · *rainbow* (hue along the keyboard) · *velocity* (soft = dim teal, hard = bright red) |
| Colour | ten swatches; in split style, pick the side first |
| Split | the key where the right side starts, default C4 |
| Release | fade 0 / 0.25 / 0.6 / 1.5 s, quadratic |
| Sustain | *pedal holds*: a key released under the pedal stays lit until the pedal comes up |

The pedal is CC 64, now surfaced by `MidiDecoder(pedal=True)` as a `PedalEvent` (edges
only, so half-pedalling does not flicker); the practice state machine still ignores it.
Black keys get the same `BLACK_KEY_GAIN` as practice. Fades are drawn by the 50 Hz tick
straight to the LEDs; the screens only hear about it once a key goes fully dark, so a fade
does not push 50 state snapshots a second. Logic: `firmware/src/fingerlight/freeplay.py`.

**Calibrate** (Settings → *Calibrate keys…*). The generated map is analytic (one measured
octave span); this screen corrects it **per key**, one LED (6.94 mm) at a time:

- press a piano key — or tap ◀ key ▶ / the 88-key strip — to select it;
- the bar shows the selected key's pair **white**, the key to its left **amber**, the key
  to its right **teal**, so you can see spacing as well as position;
- **← light / light →** moves that key's pair; **← all / all →** moves every key (a bar
  glued a few mm off); reset this key / reset all;
- **Save as default** writes `~/.fingerlight/led_offsets.json`, which every later start
  loads; **Close** with unsaved changes asks once, then drops them.

Offsets ride on `StripSettings.led_offsets` and are applied in `note_to_leds`, *after*
upstream's `get_note_position` — which stays verbatim, so `verify_against_map` (D-08) still
checks the generated map, not the hand corrections. A key may move at most 12 LEDs, and its
own LED may not leave the strip. If many keys need the same growing correction toward one
end, that is a scale error: re-measure the octave span and re-run `tools/gen_map.py`
rather than nudging 40 keys ([firmware](firmware.md), `calibration.py`).

While Calibrate is open, every screen redirects to it and the tabs are disabled — the
bar cannot be half in calibration. A running attempt is paused on entry.

Verified on the PC (Chromium, 800×480): Free play in every style, with and without a score
open, Settings and Calibrate — nothing outside the panel, no button under 52 px except the
88 overview cells (a secondary way to select; the piano is the primary one), no console
errors; in the preview the bar lights, holds under Shift-as-pedal, fades to dark, and shows
the calibration colours. Not verified: any of it on the Pi.

## See also

[index](index.md) · [pi-setup](pi-setup.md) · [decisions](decisions.md) D-30, Q-07 · [firmware](firmware.md) for the state machine the app drives ·
[hardware](hardware.md) §6 for the panel and the Pi · [simulation](simulation.md) for what the bar itself does ·
[log](../log.md) 2026-09-22.
