---
name: provenance
description: What this project takes from Piano-LED-Visualizer, what it takes from ScoreForge, and what is new
date: 2026-09-16
aliases: [Provenance, upstream-baseline, 02-upstream-baseline, scoreforge-merge, 04-scoreforge-merge]
tags: [reference]
---

# Provenance

Three sources feed this project: upstream **Piano-LED-Visualizer** (onlaj, MIT), the
owner's prior project **ScoreForge** (own work, no external licence), and code written
for this project with no prior art in either. This note maps every substantial piece of
the codebase to one of those three, states the relationship precisely, and corrects a
licence error that shaped two earlier decisions.

## Relationship labels

- **VENDORED** — third-party code sitting in this repo as-is.
- **TRANSCRIBED** — upstream logic re-typed into our own module, behaviour-identical.
- **SETTINGS-ONLY** — upstream code untouched, driven by different configuration values.
- **PORTED-STRUCTURE** — algorithm/architecture followed, no code imported or copied.
- **NEW** — no prior art in either project.

## The licence correction

**Piano-LED-Visualizer is MIT, not GPL-3.0.** Project docs said GPL-3.0 in seven places
until today. That error is now fixed everywhere and is recorded here so it stays visible.

**VERIFIED**, both ways, 2026-09-16:

- The pinned clone's licence file, `C:\Users\Kevin\dev\Piano-LED-Visualizer\LICENSE` at
  commit `b502ad67`: `MIT License / Copyright (c) 2022 onlaj`.
- `GET https://api.github.com/repos/onlaj/Piano-LED-Visualizer` (unauthenticated):
  `"spdx_id": "MIT"`.

MIT requires one thing on redistribution: keep the copyright notice and the licence text
with the software. That is the entire obligation — no source-disclosure requirement, no
copyleft, no restriction on closing the source of anything built on top. This is what
the licence text plainly says; anything past that (e.g. how it interacts with a specific
product plan) is a legal judgement call this note does not make.

Two earlier decisions cited the wrong licence as part of their reasoning:

- **D-23** (separate PC-side score compiler, not on the Pi) partly justified the process
  boundary as keeping "GPL inheritance off the compiler." Under MIT there is no
  inheritance to keep off anything. The decision still stands — the other reasons (JVM
  heap for OMR, wanting OMR offline) are unaffected — but that reason is void.
- **D-01** listed "wanting a non-GPL product" as a condition that would reverse a choice.
  Moot: the product was never GPL-encumbered.

Both corrections, and the seven-place doc sweep, are logged in [log](../log.md) under
2026-09-16. See [decisions](decisions.md) for D-01 and D-23 in full.

## Bucket 1 — from Piano-LED-Visualizer (upstream, MIT)

Upstream is ~15.8k lines of Python already doing roughly 60% of this project: LED
driver, MIDI I/O, a Flask web UI, an LCD menu, and a practice/learning-mode loop. The
clone lives outside the project tree at `C:\Users\Kevin\dev\Piano-LED-Visualizer`
(Box cannot sync a `.git` directory) and is **not yet vendored into this repo** — wiring
it in is the main remaining work.

| Item | Where in this repo | Relationship | Evidence |
|---|---|---|---|
| Note→LED position formula | `firmware/src/fingerlight/notemap.py:72-88`, `get_note_position()` | TRANSCRIBED | upstream `lib/functions.py:528-545`; diffed below |
| LED position, actual firmware use | not yet present | SETTINGS-ONLY (planned) | `data/led_map.csv`, 88/88 notes match; see below |
| ~~`rpi_ws281x` strip driver~~ | not yet present | **DROPPED 2026-09-22 (D-28)** — the library cannot drive a Pi 5; the bar will be driven by SPI-encoded bytes on GPIO10 | `docs/02-upstream-baseline.md:113` (source note, being retired) |
| MIDI port discovery/recovery (`lib/midiports.py`) | not yet present | VENDORED (planned reuse) | upstream `find_and_set_piano`:294, `_recover_piano_output`:267 |
| MIDI hot path (`lib/midi_event_processor.py`) | not yet present | VENDORED (planned reuse) | upstream `process_midi_events`:40 |
| Flask web UI, settings persistence | not yet present | VENDORED (planned reuse, extend) — the app is ours (D-30), upstream's routes are the starting point | upstream `webinterface/` |
| ~~LCD menu~~ | not yet present | **DROPPED 2026-09-22 (D-27)** — there is no SPI LCD HAT on this build; the 5" DSI touchscreen replaces it | upstream `lib/menulcd.py` |
| Practice loop skeleton, wait-for-note, mistake logging | not yet present | VENDORED (planned reuse, extend) | upstream `lib/learnmidi.py:401` `learn_midi()` |

### The transcription, verified by diff

**VERIFIED** — diffed the two functions directly.

Upstream, `lib/functions.py:528-545` (pinned clone, commit `b502ad67`):

```python
def get_note_position(note, ledstrip, ledsettings):
    note_offsets = ledsettings.note_offsets
    note_offset = 0
    for threshold, offset in note_offsets:
        if note > threshold:
            note_offset += offset
    note_offset -= ledstrip.shift
    density = ledstrip.leds_per_meter / 72
    note_pos_raw = int(density * (note - 20) - note_offset)
    if ledstrip.reverse:
        return max(0, ledstrip.led_number - note_pos_raw)
    else:
        return max(0, note_pos_raw)
```

This repo, `firmware/src/fingerlight/notemap.py:72-88`:

```python
def get_note_position(note: int, s: StripSettings) -> int:
    note_offset = 0
    for threshold, offset in s.note_offsets:
        if note > threshold:
            note_offset += offset
    note_offset -= s.shift
    density = s.leds_per_meter / 72
    note_pos_raw = int(density * (note - 20) - note_offset)
    if s.reverse:
        return max(0, s.led_count - note_pos_raw)
    return max(0, note_pos_raw)
```

**What differs:** the loop body, the comparison, and the arithmetic expression are
byte-identical. Only three things changed, all cosmetic: upstream's two objects
(`ledstrip`, `ledsettings`) are merged into one `StripSettings` dataclass `s`; the field
`led_number` is renamed `led_count`; and type hints plus a docstring were added. **What
does not differ:** the formula, the operator precedence, the truncation via `int()`, and
the `if/else` branch structure. This module's own docstring
(`firmware/src/fingerlight/notemap.py:1-18`) states the same thing in its own words: "we
do not fork upstream's LED driver," kept "byte-for-byte faithful on purpose... If
upstream changes this function, this copy is what tells us." The transcription exists to
prove the fix below is a settings change, and to serve as a regression test — not for any
licensing reason, since MIT already permits a straight fork.

### The fix itself is settings-only

Upstream's uniform 13.889 mm/note assumption drifts from the real 13.675 mm key-tail
pitch and, at the top of the board, computes an out-of-range LED index. The correction
ships as two values upstream's own settings schema already accepts:

- `shift = -1` (upstream default: `0`)
- `note_offsets = [[47, 1], [79, 1]]` (upstream default: `[[92, 2], [55, 1]]`)

No line of `get_note_position` changes. `tools/gen_map.py` derives these values
analytically from the measured octave span, and `notemap.py`'s `verify_against_map()`
confirms all 88 notes (A0–C8) reproduce `data/led_map.csv` with **0 mismatches** — the
transcribed copy and the generated settings agree completely.

## Bucket 2 — from ScoreForge (owner's own prior project, no licence constraint)

ScoreForge (formerly "Guitar Tab Generator") is a local audio→score pipeline: Demucs
(`htdemucs_6s`) separates a piano stem, a bytedance transcription model (Basic Pitch as
fallback) turns it into MIDI, cleanup passes fix transcriber artifacts, and MuseScore can
render an optional PDF. It is vendored whole under `scoreforge/` and is **not yet wired**
into the new score compiler.

| Item | Where in this repo | Relationship | Evidence |
|---|---|---|---|
| Whole ScoreForge tree | `scoreforge/` | VENDORED, not wired in | vendored 2026-09-15 from `ScoreForge.zip` |
| Piano fingering DP structure | `score/src/fingerlight_score/fingering.py` | PORTED-STRUCTURE | see below |
| MIDI note-merge, tempo-octave fix | not yet wired (planned reuse) | VENDORED (planned) | `scoreforge/src/scoreforge/tab.py:348,401` |
| Grand-staff MIDI writer | not yet wired (planned reuse) | VENDORED (planned) | `scoreforge/src/scoreforge/tab.py:999` |
| Pipeline/progress-callback skeleton | not yet wired (planned reuse) | VENDORED (planned) | `scoreforge/src/scoreforge/pipeline.py:47` |
| Audio→MIDI 4th input path | not yet wired (planned reuse) | VENDORED (planned) | `scoreforge/src/scoreforge/transcribe.py:128`, `separate.py:26,35` |
| MusicXML ingest | `score/src/fingerlight_score/musicxml.py` | NEW | ScoreForge has no MusicXML reader |
| OMR (PDF/photo → score) | `score/src/fingerlight_score/omr.py` | NEW | ScoreForge has no OMR at all |

### The fingering DP: a port, not an import

**VERIFIED** — grepped every non-relative import across all of `score/src`:

```
emit.py:      json, os, pathlib.Path, typing
fingering.py: itertools, dataclasses, typing
hands.py:     typing
musicxml.py:  zipfile, bisect, dataclasses, typing,
              defusedxml.ElementTree (try) -> xml.etree.ElementTree (except ImportError)
notes.py:     dataclasses, typing
omr.py:       os, shutil, subprocess, tempfile, zipfile, dataclasses, fractions,
              defusedxml.ElementTree (try) -> xml.etree.ElementTree (except ImportError),
              xml.etree.ElementTree (tree construction)
```

Every import is stdlib. The one non-stdlib name, `defusedxml`, is a soft dependency —
both `musicxml.py` and `omr.py` try it first and fall back to the stdlib
`xml.etree.ElementTree` on `ImportError`. `score/src` never imports `scoreforge`,
`guitarpro`, or any ML dependency. This is by design, stated in
`score/src/fingerlight_score/fingering.py`'s own module docstring: "NOT a code import:
this module is stdlib-only and must never import `scoreforge` (it pulls in
`guitarpro`/`basic_pitch` and pins Python <3.12; the ambient interpreter here is
3.12.7)." Because the DP is a port rather than an import, ScoreForge's `guitarpro`
dependency and its Python `<3.11` pin (`scoreforge/pyproject.toml` — Demucs pins
`torchaudio<2.1`, Basic Pitch pins `tensorflow<2.15.1`, neither has cp312+ wheels) never
apply to `score/`, which is why the score compiler runs on the ambient Python 3.12.7.

`fingering.py`'s structure follows `scoreforge/src/scoreforge/tab.py:245`
(`assign_frets`): group simultaneous notes into chord events, enumerate a bounded set of
candidate voicings per event, run a forward DP with a pairwise transition cost between
consecutive events, backtrack for the cheapest global path. Same shape, different state:

| Guitar (`tab.py`) | Piano (`fingering.py`) |
|---|---|
| candidate = string/fret assignment | candidate = finger/hand assignment |
| transition cost = Δ average fret | transition cost = Δ hand position |
| span = max−min fret in a voicing | span = hand span in semitones (comfort table) |
| — | new: thumb-under/crossing cost, hand (L/R) assignment cost |

The inversion that matters: `docs/research/fingering-survey.md §1` surveys
jgollub1/guitar_dp's finger-identity transition cost — predict where finger *n* lands
given the previous finger's position — and **recommends against it for guitar**, because
a guitar tab has no finger numbers to output. For this project that recommendation
inverts: finger identity *is* the output, because finger identity is the LED colour
(D-12). `fingering.py`'s own docstring makes the same point: "the guitar_dp survey
flagged this... idea as not portable to guitar but exactly the right shape for piano."

## Bucket 3 — new work (no prior art in either project)

No upstream or ScoreForge equivalent exists for any of these.

| Item | Where in this repo | Evidence |
|---|---|---|
| Analytic `shift`/`note_offsets` generator | `tools/gen_map.py` | derives settings from measured octave span |
| 2-LED hand+finger colour encoding | `firmware/src/fingerlight/notemap.py`, `note_to_leds()` | hand pixel + finger pixel per key, D-12 |
| Device-independent light-score format | `docs/score/lightscore-format.md` | LED indices deliberately not stored |
| Hand assignment (L/R) | `score/src/fingerlight_score/hands.py` | no analogue in guitar tab or upstream |
| MusicXML ingest | `score/src/fingerlight_score/musicxml.py` | ScoreForge has no MusicXML reader |
| OMR arm (PDF/photo → score) | `score/src/fingerlight_score/omr.py` | ScoreForge has no OMR; D-04 gap |
| MIDI ingest as a direct input | `score/src/fingerlight_score/` | new input path alongside the audio one |
| Score-informed audio verification | `docs/firmware/audio-verification-benchmark.md` | no prior art |
| Calibration mode (A0/C4/C8 confirm) | planned, not yet present | upstream has no calibration step |
| Practice state machine | planned, extends upstream's loop | upstream has per-hand colour only, no fingering |
| Level shifter / power-injection design | `docs/hardware/hardware-index.md` | upstream wires GPIO18 straight to DIN |
| Capacitive-touch-panel mount | `docs/01-mechanical-and-hardware-survey.md` | upstream's instrument has a plain shelf |

## Summary table

| Bucket | Lines/scale | Licence posture | Wired in today? |
|---|---|---|---|
| Piano-LED-Visualizer | ~15.8k lines, MIT | permissive, notice-only obligation | not yet — planned reuse |
| ScoreForge | ~2.7k lines, owner's own work | no external constraint | vendored, not yet wired |
| New | growing | this project's own | in progress |

## Code maps

Kept at full granularity because this is the working reference for the next job — wiring
the firmware to upstream — and a map without line anchors is not a map. Upstream paths are
relative to `C:\Users\Kevin\dev\Piano-LED-Visualizer` at pin `b502ad67`; ScoreForge paths
to `scoreforge/src/scoreforge/`.

### Upstream — what each file does, and what we do with it

| File | Role | Us |
|---|---|---|
| `visualizer.py` | `VisualizerApp`: singleton `fcntl` lock, signal handling, main loop | **reuse** — note it takes the lock and does `os.chdir(sys.path[0])`; tests and tooling must account for both |
| `lib/component_initializer.py` | builds every subsystem | reuse |
| `lib/midi_event_processor.py` | MIDI in → LED out; **the realtime hot path** | reuse, hook our note map in here |
| `lib/led_effects_processor.py` | animation/effect tick | reuse |
| `lib/ledstrip.py` | `PixelStrip` wrapper, per-LED state arrays | reuse |
| `lib/ledsettings.py` | colour modes, `note_offsets`, backlight. **`:82,134-157`** add/remove/edit offsets at runtime | reuse — this is where our generated settings land |
| `lib/functions.py:528` | `get_note_position`, the note→LED formula | **transcribed** into `firmware/src/fingerlight/notemap.py:72-88`; driven by different settings, not forked |
| `lib/learnmidi.py` | "learning" mode: MIDI file playback + wait-for-note | **replace** — our practice state machine supersedes it |
| `lib/midiports.py` | piano/computer port discovery and recovery | reuse alongside `firmware/src/fingerlight/midi.py` |
| `lib/menulcd.py` | on-device LCD menu (~2.1k lines) | reuse as-is |
| `webinterface/` + `views_api.py` | Flask app (~2.7k lines) | reuse; extend for score selection |
| `lib/platform.py` | Pi detection, SPI enablement | reuse |

### ScoreForge — what we reuse, adapt and ignore

| Symbol | Verdict | Note |
|---|---|---|
| `tab.py:245` `assign_frets()` + `:145-150` weights | **adapt** | the forward-DP/Viterbi skeleton → piano fingering. Ported, not imported |
| `tab.py:184` `_enumerate_event_candidates()` | adapt | per-event candidate emission; our analogue is `_enumerate_candidates` |
| `tab.py:71` `Note`, `:95` `group_chord_events()`, `:330` `load_notes_from_midi()` | reuse | the note/event model the DP runs on |
| `tab.py:999` `build_piano_midi()` | reuse | multi-track cleaned-MIDI writer; already the piano output |
| `tab.py:348` `merge_repeated_notes()` | reuse | fixes transcriber oversegmentation; instrument-agnostic |
| `tab.py:401` `correct_tempo_octave()` | reuse | fixes `pretty_midi` tempo-octave errors; instrument-agnostic |
| `transcribe.py:128` `transcribe_piano()` | reuse | bytedance model, Basic Pitch fallback. Soft dependency |
| `separate.py:26,35` | reuse | `htdemucs_6s` piano stem; skipped entirely for solo recordings |
| `melody.py:45` `reduce_to_melody()` | reuse | pure; useful for a "light the melody only" practice mode |
| `render.py:34` `render_pdf()` | reuse | MuseScore shell-out, soft-skips if absent |
| `util.py` `find_executable()` | reuse | resolves CLI tools next to `sys.executable` — needed under a GUI |
| `pipeline.py:47` `run_pipeline()` | adapt | stage skeleton + `progress(stage, message)` callback |
| `webui.py:104,192` + `static/` | adapt | FastAPI + SSE job runner. Compare against upstream's Flask UI before adopting |
| `tab.py` gp5 / ascii-tab / capo paths | ignore | guitar-only |
| `chord.py`, `tests/test_chord.py` | ignore | guitar-only — `pipeline.py:146` skips it for piano |

Four claims about ScoreForge were **CONFIRMED** by reading it end to end: it has a working
piano path (`pipeline.py:96,130,182`); `assign_frets()` is a true global DP and not greedy
(`tab.py:184-330`, with `tests/test_tab.py:119` asserting look-ahead); its weights come
from noahbaculi/guitar-tab-generator (stated in the survey and in `tab.py` comments); and
chord detection is guitar-only (`pipeline.py:146`).

See [index](index.md) for the project map, [decisions](decisions.md) for D-01/D-08/D-12/D-15/D-23 in full,
and [log](../log.md) for the 2026-09-16 licence-correction entry.
