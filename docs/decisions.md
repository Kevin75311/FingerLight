---
name: decisions
description: Every design decision made so far, why, who made it, and what would reverse it, plus the status of every open question
date: 2026-09-16
aliases: [Decisions, decision-log, 03-decision-log, Open questions]
tags: [decisions]
---

# Decisions and open questions

`[USER]` = the user decided it. `[DESIGN]` = proposed here, not yet contested.

## Two corrections (2026-09-16)

The docs used to say Piano-LED-Visualizer is GPL-3.0. It is **MIT** — **VERIFIED** twice: the
pinned clone's `LICENSE` at `b502ad67` reads `MIT License, Copyright (c) 2022 onlaj`, and an
unauthenticated `GET https://api.github.com/repos/onlaj/Piano-LED-Visualizer` returns
`license.spdx_id == "MIT"`. This voids one reversal condition on **D-01** and one rationale
clause on **D-23** (both marked below). Neither decision changes — D-23 stands on its other
grounds. The error is kept visible here rather than silently removed; see [log](../log.md)
2026-09-16 for the full trail.

**M1 is measured, not assumed: 164.8 mm** (was an assumed 164.1 mm industry-standard figure).
The map and settings were regenerated at the measured value; see D-08 below and [log](../log.md)
2026-09-15 ("M1 landed").

## Decisions

### Foundation

| # | Decision | Why | Who | What would reverse it |
|---|---|---|---|---|
| D-01 | Fork `onlaj/Piano-LED-Visualizer` | Already does LED driving, MIDI I/O, web UI, LCD menu, a practice loop — roughly 60% of the work | [USER] | A non-Python realtime path. *(The other listed reversal reason, "wanting a non-GPL product," is **void** — upstream is MIT, corrected 2026-09-16.)* |

### Hardware

| # | Decision | Why | Who | What would reverse it |
|---|---|---|---|---|
| D-02 | Raspberry Pi Zero 2 W | Upstream's supported class; enough for LED + MIDI + the score-informed audio path | [USER] | Wanting OMR or blind transcription on-device → Pi 4/5 |
| D-03 | Mechanical build follows upstream | Profile + diffuser taped behind the keys, printed Pi case, standoffs, braid | [USER] | — |
| D-07 | 176 × WS2812B/SK6812 @ 144 LED/m | Only density where LEDs/key ≈ integer (1.97) *and* 176 LEDs ≈ the 1219 mm playing width. Matches upstream defaults exactly | [DESIGN] | A custom PCB bar (see D-14) |
| D-09 | Silicone T0515 profile over aluminium | The PX-S1100 panel behind the keys is capacitive touch; grounded metal nearby detunes it | [DESIGN] | Measuring and finding a metal-safe run ≥10 mm clear of every button |
| ~~D-10~~ | ~~Stock PWM wiring (LED on GPIO18, LCD HAT on SPI0), audio in over USB~~ **— superseded by D-27/D-28 on 2026-09-22; its reversal clause is what happened.** Audio in over USB survives | Keeps every upstream pin number, diagram and photo valid; the OTG hub is already required | [DESIGN] | Dropping the LCD HAT and the hub → move LEDs to SPI0/GPIO10, audio to I²S/PCM1808 |
| D-11 | Add a 74AHCT125 on the data line | Upstream drives 5 V DIN from 3.3 V GPIO directly; marginal, and fails as the strip warms | [DESIGN] | — |
| D-14 | Custom LED bar PCB deferred, not rejected | Emitters at exact tail centres remove the 3.42 mm residual and allow 3 LEDs/key; but prove the concept on strip first | [DESIGN] | After v1 works and the strip's residual proves annoying |
| D-19 | Skip the BCM 12 cover-detect switch | The PX-S1100 has no key lid | [DESIGN] | — |
| D-20 | 5 V 8 A PSU, power injected at three points | 176 LEDs full-white is 10.6 A; upstream's 6 A assumes a 50% cap. Thin flex copper browns the far end on a single end feed | [DESIGN] | Capping global brightness hard in firmware and accepting 6 A |

### Firmware

| # | Decision | Why | Who | What would reverse it |
|---|---|---|---|---|
| D-06 | Note sensing: USB MIDI + BLE MIDI + audio fallback | BLE for convenience (WU-BT10 already owned), audio so it also works on an acoustic | [USER] | — |
| D-08 | Analytic `shift`/`note_offsets` instead of hand-nudged | Reduces worst-case centring error from 8.89 mm to 3.42 mm (the LED-pitch quantisation floor). *(Figures were first computed at the assumed M1 = 164.1 mm; regenerated at the measured M1 = 164.8 mm, and again on 2026-09-21 for a centred tape (D-25): worst error 3.45 mm, `shift = -2`, `note_offsets = [[21,-1],[51,1],[96,1],[107,-1]]` — [hardware](hardware.md) §2.1a.)* Proven empirically by B1: all 88 notes reproduce `data/led_map.csv`, 0 mismatches | [DESIGN] | Nothing — it is strictly better and costs three settings values |
| D-12 | 2-LED encoding: left pixel = hand, right pixel = finger hue | 10 classes (5 fingers × 2 hands) are not distinguishable as 10 hues in peripheral vision; each key owns ~2 LEDs, so split the roles | [DESIGN] | A custom PCB with 3+ LEDs/key allows richer encoding |
| D-13 | No pure blue in the finger palette | Dimmest channel per unit drive, and it dies on a matte black keytop | [DESIGN] | — |
| D-16 | Audio path is score-informed verification, not transcription | The expected pitches at each moment are already known, so it is a small tuned filterbank against ≤10 candidates, not blind polyphonic transcription | [DESIGN] | Wanting free-play transcription with no score loaded |
| D-17 | Per-key-type brightness gain (black ×1.4–1.7) | Matte black keytops read far dimmer than ivory-textured white under the same LED | [DESIGN] | Measurement on the built bar showing otherwise |
| D-18 | Ship a calibration mode (light one LED, confirm at A0/C4/C8) | Three confirmations pin the map empirically; beats trusting any spec sheet. Built as B4: fits `error_mm = a + b·position` so a mis-glued bar (uniform offset) is distinguishable from a wrong M1 (error grows with position) | [DESIGN] | — |

### Score compiler

| # | Decision | Why | Who | What would reverse it |
|---|---|---|---|---|
| D-04 | Score input: MIDI + MusicXML + PDF/photo via OMR | Most real sheet music is a PDF | [USER] | — |
| D-05 | Fingering auto-generated algorithmically | Fingering annotations are absent from almost all scores | [USER] | — |
| D-15 | OMR runs offline on the PC, not the Pi | Audiveris is a JVM app wanting 2 GB+ heap; Zero 2 W has 512 MB. Score compilation is not realtime — the split costs nothing | [DESIGN] | Moving to a Pi 4/5 with ≥4 GB |
| D-21 | Vendor ScoreForge's piano path into `scoreforge/` unmodified; guitar + bulk to an archive zip | Keeps provenance readable and the package importable, without forking code before Part 3 is designed | [DESIGN] | Deciding to hard-fork into a clean piano-only package once the Part 3 design settles |
| D-22 | Piano fingering = port ScoreForge's DP, adding finger identity to the state | Answers Q-04 with working, tested code rather than a new algorithm; the guitar DP is the same shortest-path formulation Sayegh (1989) established for fingering generally. Delivered as A1; a left-hand mirroring bug was found post-delivery and fixed (see [log](../log.md) 2026-09-15) | [DESIGN] | The ported DP producing unplayable fingering on real scores → try minimax Viterbi (Hori & Sagayama) or a learned model |
| D-23 | Score compiler stays a separate PC-side process, talking to the Pi by data file **— reversal condition now reachable: an 8 GB Pi 5 can host a JVM. Not acted on; see [hardware](hardware.md) §6.3** | D-15 already put OMR offline, and the compiler needs a JVM plus 2 GB+ of heap the Pi does not have. *(A second reason recorded here — keeping GPL-3.0 firmware inheritance off the compiler — is **void**: upstream is MIT, corrected 2026-09-16. The decision stands on the reason above.)* | [DESIGN] | A Pi capable of running Audiveris (2 GB+ heap), which would remove the reason to split them. *(The originally recorded reversal — "folding the compiler in would make it a derivative work" — was a GPL concern and is void under MIT.)* |
| D-24 | Accept audio → score as a fourth input alongside MIDI/MusicXML/PDF | ScoreForge already implements it end to end; it costs nothing to keep and covers "I can play it but have no score" | [DESIGN] | — |
| D-25 | Tape centred on the playing width, not at the min-error origin | `gen_map.py`'s error-minimising origin sat 7.75 mm left of A0 and left 9.76 mm bare at the top — visibly off-centre on the instrument. Centring costs **0.05 mm** of worst-case centring error (3.40 → 3.45 mm) and keeps 88/88 unique LEDs. Decided by Kevin 2026-09-21 | [DESIGN] | A measurement showing the residual matters more than the symmetry |
| D-27 | **Platform: Raspberry Pi 5 + 5" DSI touchscreen (800×480) + USB microphone**, replacing the Zero 2 W, the SPI LCD HAT and the OTG hub | Kevin's call, 2026-09-22. Removes both Zero 2 W compute limits, gives four USB ports (piano + mic, no hub) and a real control surface. Costs: the LED driver must change (D-28), the Pi needs its own 5 V/5 A PD supply, and the build goes from ≈125–155 to ≈195–255 USD | [USER] | — |
| D-28 | **LED data moves to SPI0/GPIO10, driven by SPI-encoded bytes** (not `rpi_ws281x`) | `rpi_ws281x` DMAs into BCM2835 PWM/PCM; the Pi 5's RP1 breaks that. Of the three Pi 5 paths (SPI bytes, RP1 PIO, external MCU) only SPI needs no part we were not already buying — and D-10's own reversal clause named this move. 176 LEDs ≈ 1.6 kB/frame, inside the stock `spidev` limit. **UNVERIFIED on hardware** — first bench task | [DESIGN] | SPI flickering on the real board → RP1 PIO, then a Pico |
| D-29 | **The microphone is a backup input, not a second opinion** | With USB-MIDI connected the piano reports exactly which key went down and how hard; the mic exists for instruments with no MIDI out. MIDI is the timing reference and the truth; audio never overrides it, and with headphones on, the mic hears nothing | [USER] | An acoustic-piano build, where the mic becomes the only input |
| D-30 | **The app is a web app in kiosk-mode Chromium on the touchscreen** | One codebase serves the panel and a phone; upstream's Flask UI is a starting point rather than dead weight; layout iteration is far cheaper in HTML than in Qt/Kivy; a Pi 5 has ample room for Chromium beside the LED loop. Closes Q-07 | [USER] | — |
| D-26 | Build a simulation (Part 4) before the hardware exists | The CAD arrived before the bar did; a scene driven by the real firmware checks the map against the instrument's geometry rather than a spreadsheet, and it immediately found an off-centre tape, a `gen_map` sign bug and the board's 21 mm overhang | [DESIGN] | — (it is additive; the firmware does not depend on it) |

## Open questions

Status as of 2026-09-16, re-derived from [log](../log.md) and the implementation plan — not copied
from the old decision log, which was stale on five of these.

| # | Question | Status | Where answered |
|---|---|---|---|
| Q-01 | Octave span M1 on the actual instrument | **CLOSED** — measured M1 = 164.8 mm; map and settings regenerated, worst error 3.4 mm | [log](../log.md) 2026-09-15 ("M1 landed") |
| Q-02 | Panel geometry M3–M6 | **CLOSED on reduced scope** — Kevin owns the mechanical build and delivers a CAD file; the only mechanical parameter software tracks is M1 | [log](../log.md) 2026-09-15 ("status audit"); [hardware](hardware.md) |
| Q-03 | What is in ScoreForge | **CLOSED** — digested and vendored | [log](../log.md) 2026-09-15 |
| Q-04 | Fingering algorithm family | **CLOSED** — DP, per D-22. Weights and the finger-identity cost still need tuning against real scores | D-22; [score](score.md) |
| Q-05 | Light-score file format and push mechanism to the Pi | **CLOSED** — JSON schema `lightscore-v1.schema.json`, built as A0 | [log](../log.md) 2026-09-15; [score](score.md) |
| Q-06 | How far ahead to preview upcoming notes, and how to show it without confusing "now" | **OPEN** — B3 (practice state machine) leaves preview depth as a bare parameter; the UX question itself is undecided | [firmware](firmware.md) |
| Q-07 | Whether the web UI or the LCD menu is the primary control surface for practice | **CLOSED** — D-30: a web app in kiosk Chromium on the 5" DSI panel, the same app reachable from a phone. The LCD menu is not carried over | D-27, D-30; [hardware](hardware.md) §6 |
| Q-08 | OMR: Audiveris vs. alternatives, MusicXML → note-model ingest | **CLOSED for implementation, UNVERIFIED end-to-end** — A4 (`omr.py`) implements the Audiveris subprocess wrapper and, more importantly, the post-OMR staff-splitting cleanup proven load-bearing (6 notes/0-on-staff-2 without it vs. 9 notes/3-on-staff-2 with it). The subprocess call itself has never run against a real JVM/Audiveris install on this machine, so treat the choice as made and the ingest path as built, but not hardware-proven | A4; [log](../log.md) 2026-09-16; [score](score.md) |
| Q-09 | Hand assignment: inside the fingering DP, or a separate pass before it? | **CLOSED** — separate pass, A2 (`hands.py`), three modes (`staff`/`pitch`/`auto`), `auto` is default | [log](../log.md) 2026-09-16; [score](score.md) |
| Q-10 | Does the note→LED map become `note → [(led, weight)]`? | **OPEN** — prototyped in [simulation](simulation.md): a weighted profile cuts the apparent-centre error from 6.85 mm to 0.68 mm and crosstalk from 16% to 1% at identical optics. But upstream's `shift`/`note_offsets` formula maps one note to one LED and **cannot express weights**, so adopting it means the overlay becomes a PLV-compatibility fallback rather than the source of truth (D-08 territory) | [simulation](simulation.md); [log](../log.md) 2026-09-21 |
| Q-11 | How are hand and finger encoded once a key is one soft blob? | **OPEN** — D-12's two-pixel split (hand pixel + finger pixel) cannot survive a diffused blob. Options not yet compared: colour = finger with hand as a dim leading edge; hand = blob colour with a brighter finger core; or keep two-pixel mode as a toggle. A taste call, and it blocks Q-10 | [simulation](simulation.md); D-12, D-13 |
| Q-12 | What blur does the diffuser Kevin bought actually produce? | **OPEN** — the sim assumes `σ = 0.45 × gap`, which puts the continuity threshold at a 7 mm gap. One photograph of a single lit key through the real profile is enough to fit the coefficient, after which the gap and the band width can be re-chosen | [simulation](simulation.md); [hardware](hardware.md) §5 |

Reasoning on the one judgement call (Q-08): "closed" here means the *decision and the code*
exist — Audiveris was never seriously contested against an alternative, and the ingest path
(OMR → MusicXML → A3) is written and unit-tested with a synthetic fixture proving the cleanup
step matters. It is not marked fully closed because the one thing that would actually validate
the choice — running real Audiveris output through the wrapper — cannot happen without a JVM,
which isn't on this machine. Calling it OPEN would understate how much is done; calling it
CLOSED with no caveat would overstate confidence in an untested subprocess boundary.

## See also

[index](index.md) · [log](../log.md) for the session-by-session trail behind every closure above · [hardware](hardware.md)
· [firmware](firmware.md) · [score](score.md) · [provenance](provenance.md) for what was vendored from where.
