---
name: log
description: Append-only progress log — one entry per working session
date: 2026-09-15
aliases: [Log, Progress, Worklog, 05-status-audit, status-audit]
tags: [log]
---

# Progress log

Append-only. Newest entry at the **bottom**. One entry per working session.

Entrance: [index](docs/index.md) · Current state and what's next: [README](README.md) · Decisions and open
questions: [decisions](docs/decisions.md)

## How to use this

Every session appends one `##` entry using the template at the foot of this note. Rules:

- **State evidence, not intent.** "Ran X, output Y" — never "should now work".
- **Mark verified vs. assumed** on any claim that a later session might build on.
- Link to the note that changed (`[[07-mechanical-dimension-spec]]`), so the log shows up
  in that note's backlinks.
- Never rewrite an earlier entry. Corrections go in a **new** entry that says what it
  corrects.

---

## 2026-09-15 — project set up (Parts 1 + docs)

**Done**
- Part 1 written in full → [01-mechanical-and-hardware-survey](docs/hardware.md)
- Upstream code mapped → [02-upstream-baseline](docs/provenance.md); decisions D-01…D-24 → [03-decision-log](docs/decisions.md)
- `tools/gen_map.py` written; `data/led_map.{csv,json}` generated at the assumed 164.1 mm octave
- ScoreForge delivered, digested and vendored → [04-scoreforge-merge](docs/provenance.md); Q-03 closed, Q-04 closed (DP, D-22)

**Open at end of session:** Q-01, Q-02, Q-05, Q-06, Q-07, Q-08, Q-09.

## 2026-09-15 — status audit + plan + dimension spec

**Done**
- Audited the tree against the docs → [05-status-audit](log.md)
- Wrote the ordered plan → [06-implementation-plan](docs/index.md)
- Wrote the full dimension checklist in four tiers → [07-mechanical-dimension-spec](docs/hardware.md)
- Created this log; rewired [index](docs/index.md) as the single entrance

**Found — three gaps between docs and tree** (details in [05-status-audit](log.md) §2):

| Finding | Evidence | Status |
|---|---|---|
| `external/Piano-LED-Visualizer/` does **not** exist, though [HANDOFF](docs/index.md) §3 says it is cloned at `b502ad67` | `find` over `Box/Projects`, no match | **VERIFIED** — blocks checking [02-upstream-baseline](docs/provenance.md)'s file:line refs |
| No git repo anywhere in the project | no `.git` found | **VERIFIED** |
| ScoreForge only partially importable here — `pyproject.toml` wants Python `>=3.11,<3.12`, PATH has **3.12.7** | `pytest`: `test_chord.py` 19 passed; `test_tab.py` + `test_melody.py` fail collection, `ModuleNotFoundError: guitarpro` | **VERIFIED** |

**Found — one doc inconsistency:** [01-mechanical-and-hardware-survey](docs/hardware.md) gives worst-case
centring error as both 3.42 mm and 3.47 mm. Not a contradiction: **3.4722 mm** is the
quantisation floor (LED pitch 6.9444 ÷ 2), **3.42 mm** is what the generated map achieves.
The survey should say so. *(not yet edited)*

**Verified working:** `python tools/gen_map.py 164.1` reruns and reproduces Part 1's
published figures exactly — worst error 3.42 mm, `shift = -1`,
`note_offsets = [[47,1],[79,1]]`, 88/88 unique primary LEDs.

**Next:** [06-implementation-plan](docs/index.md) Step 0 (git init, re-clone upstream at the pin,
3.11 venv), and ask Kevin for M1 + the overhead panel photo.

## 2026-09-15 — M1 landed; firmware B1–B3 built; mechanical handed off

**Scope change (Kevin):** he owns the mechanical build and will deliver a CAD file. The
software tracks **one** mechanical parameter, M1. [07-mechanical-dimension-spec](docs/hardware.md) rewritten
to match; [00-measurements](docs/hardware.md) collapsed to M1; Q-02 closed on those terms.

**M1 = 164.8 mm.** Raw reading "5 octaves = 82.4mm, one octave ≈ 16.5" is a **unit slip** —
82.4 mm would make an 88-key keyboard 122 mm wide against a 1322 mm cabinet. Read as
**82.4 cm** it gives 164.8 mm/octave and a 1224.2 mm playing width, which fits. Used the
cm reading; recorded in [07-mechanical-dimension-spec](docs/hardware.md).

| Step | Result |
|---|---|
| Regenerated map at 164.8 | 88/88 unique LEDs, range 2–175, worst error **3.4 mm**; correction simplifies to `shift=0`, `note_offsets=[[63,1]]` |
| Step 0.2 upstream clone | **done** — `HEAD=b502ad67ffdc4682…` (2026-08-30), matches the claimed pin |
| Step 0.3 citation check | **done** — `lib/functions.py:528` is `get_note_position` verbatim as [02-upstream-baseline](docs/provenance.md) quotes it |
| Step 0.4 3.11 venv | **not needed** — the DP is being *ported*, not imported, so `guitarpro` and the `<3.12` pin are sidestepped |
| Step 0.1 `git init` | **not done — Kevin's call** (git inside a Box-synced folder) |

**Box fought the clone.** Cloning into `external/` made Box rename the folder to
`external (jykevinhuang@ucla.edu)` — its conflict marker for an unsyncable `.git`. Moved
the clone to `C:\Users\Kevin\dev\Piano-LED-Visualizer`, outside Box. `external/` no longer
exists in the project; [HANDOFF](docs/index.md), `firmware/README.md` and [firmware-index](docs/firmware.md) updated.

**Built (all verified by test run, no hardware):**

- **A0 — light-score format, Q-05 CLOSED.** `lightscore/schema/lightscore-v1.schema.json`
  + two fixtures. Both validate; a negative control (finger=9) is correctly rejected, so
  the validator can fail.
- **B1 — note map.** `firmware/src/fingerlight/notemap.py`. **All 88 notes reproduce
  `data/led_map.csv`, 0 mismatches.** D-08 proven empirically.
- **B2 — colour encoding.** `colors.py`, three candidate palettes. **Palette choice is a
  taste call, left to Kevin.**
- **B3 — practice state machine.** `lightscore.py` + `practice.py`.
- **A1 — fingering DP port:** delegated, in progress.
- `tools/check_vault.py` — vault link checker; 24/24 notes reachable from [index](docs/index.md), 0 broken.

**Evidence**
```
python -m pytest firmware/tests -q   ->  54 passed in 0.37s
python tools/check_vault.py          ->  24/24 reachable, 0 broken, EXIT=0
```

**Three defects my own tests caught (all fixed):**

| Defect | How it surfaced |
|---|---|
| Palette `distinct` finger 5 was blue-dominant — violates D-13 | automated no-pure-blue check |
| Palette `anchors` fingers 2 and 3 only 60 apart — indistinguishable | separation-floor check |
| I asserted upstream's *defaults* overrun the strip; they don't | test failed, forcing a re-measurement |

**Correction to [HANDOFF](docs/index.md) §4.2** (measured, not argued): the "asks for LED 176" overrun
belongs to the **no-offset** formula. Upstream's actual defaults stay in range but are
worse in a quieter way — **G#6 and A6 land on LED 143 together** and become
indistinguishable, with a 2-LED (~14 mm) drift at the top. Both pinned as regression tests.

**Verified vs. assumed**
- VERIFIED: the pin, the cited line, the 88-note map agreement, all 54 tests, the three
  settings-variant comparison, vault connectivity.
- ASSUMED: M1 is a **single** measurement (error ÷5 but unconfirmed); `BLACK_KEY_GAIN=1.5`
  (D-17) unmeasured; palette readability in peripheral vision untested — needs the bar.

**Next:** finish A1, then A2 (Q-09 hand assignment), then A3 MusicXML. B4–B6 need
hardware or a benchmark.

## 2026-09-15 — A1 fingering DP delivered; left-hand bug found and fixed

**A1 landed.** `score/src/fingerlight_score/` — DP ported from `scoreforge/.../tab.py:245`
with finger identity as the DP state. Ascending C major → **1-2-3-1-2-3-4-5**, confirmed
the *unique* optimum by brute force over all 5⁸ fingerings; descending → **5-4-3-2-1-3-2-1**.
Stdlib-only, no `scoreforge` import, so `guitarpro` and the `<3.12` pin never come up.

**The delivery contained a real bug, reported as "adjacent" — it was not.** The DP
fingered the **left hand as though it were a right hand**: thumb on the lowest note,
little finger on the highest. That is backwards, and it was wrong on **13 of 21** notes of
the two-hand fixture — half the output.

*Why nothing caught it:* every structural property held. All notes present, all fingers
in 1..5, no finger reused within a chord, 15/15 tests green, the schema validated. Only
the **orientation** was wrong, and no check asserted orientation. A verification's
criteria only cover what they name.

**Fix:** `_axis(pitch, hand)` maps pitch to a hand-relative axis (identity for R, negated
for L); every *direction* comparison — chord ordering, thumb-under detection — is now
stated on that axis. Distances stay absolute, so the right hand is unchanged by
construction. Threaded `hand` through `_intra_event_cost`, `_enumerate_candidates`,
`_pairwise_transition`, `_transition_cost`.

| | Before fix | After fix |
|---|---|---|
| LH orientation | thumb on lowest note ❌ | pinky low, thumb high ✅ |
| Disagreement with fixture | 13 / 21 | **7 / 21**, all legitimate alternatives |
| Hand-groups violating orientation | 4 | **0** |

**Evidence**
```
python -m pytest firmware/tests score/tests -q   ->  74 passed in 0.69s
end-to-end (notes -> DP -> lightscore -> reader -> practice) -> 8/8 events, 0 errors
fix reverted in memory -> 4 hand-groups violate  => regression tests are live
```

New `score/tests/test_hand_orientation.py` pins it, and was confirmed to **fail** when the
fix is reverted — it is not decorative. It also prints the number of groups examined, so
"0 violations" cannot mean "0 groups checked".

**Known tuning gap (flagged, not fixed):** a root C major triad comes out **1-2-4** where
a pianist plays **1-3-5**. Both cost exactly zero — neither exceeds a comfort span — so
enumeration order breaks the tie. Wants a term rewarding a natural hand shape. Cosmetic
for an LED cue; not a correctness bug.

**Also built:** `tools/preview_bar.py` → an HTML page rendering the bar through the real
firmware path, so the palette (B2) can be judged instead of guessed. Structurally verified:
2112 strip cells = 176 × 3 palettes × 4 events, 126 lit pixels = 21 notes × 2 × 3, charset
present, 0 mojibake sequences. **Not visually confirmed in a browser.**

**Verified vs. assumed**
- VERIFIED: 74 tests, the brute-force uniqueness claims (re-run), the LH orientation before
  and after, the end-to-end chain, the sabotage check.
- ASSUMED: preview page's appearance; cost weights are tuned to two scales only — they have
  not met a real piece.

**Next:** A3 MusicXML ingest, then A2 (Q-09). B4–B6 need hardware or a benchmark.

## 2026-09-16 — A3 MusicXML ingest + B4 calibration

**A3 delivered and independently verified.** `score/src/fingerlight_score/musicxml.py`.
`parse_musicxml()` → notes; `musicxml_to_lightscore()` runs the whole chain. Uses
`defusedxml` (scores come from the internet; the stdlib XML parser is vulnerable to
entity expansion), stdlib fallback.

**Verification approach — the deliberate part.** Rather than assert against expectations
I typed, I used **MuseScore 4.7.4** (already installed) as an independent implementation:

```
hand-authored .musicxml  --MuseScore-->  canonical .musicxml  (the test INPUT)
                         --MuseScore-->  .mid                 (the GROUND TRUTH)
                         --MuseScore-->  .mxl                 (container variant)
```

Read back by `score/tests/midi_ground_truth.py`, a ~70-line SMF reader written for this
and independent of the MusicXML path. **That reader was itself verified**: its tempo bytes
decode to exactly 120 bpm, and MuseScore's articulation gate ratio comes out **constant at
0.948–0.9495 across 500/1000/2000 ms notes**. A wrong tick→ms conversion could not produce
a constant ratio.

Parser vs. ground truth on the grand-staff fixture: **11 notes, `(t_ms, pitch)` sets
exactly equal**, durations equal to the notated table, staves correct, tie merged
(G4 once at t=3000, d=2000).

**The agent's own report listed four features as "implemented but verified by code
reading only".** A claim verified by reading is not verified — so I built a second
fixture (`hard-src.musicxml`) targeting exactly those, and they all hold:

| Feature | Result |
|---|---|
| grace note | skipped + warned, and **does not steal cursor time** (C4 stays at t=0) |
| mid-score tempo change | m2 at 60 bpm: half note = 2000 ms, whole = 4000 ms — correct |
| repeat barlines | **not unfolded** (MuseScore's MIDI *does* unfold: 12 notes vs our 8) + warned |
| two voices on one staff | A3 is voice 2 / staff 1 and stays on **staff 1** — voice not mistaken for staff |

**One quiet defect found and fixed by me.** A score with **no `<staff>` elements at all**
(lead sheet, bare export) landed entirely on staff 1 and was fingered as though one hand
played everything, bass included — structurally perfect output, right note count, legal
fingers, valid light-score, **only the hands wrong**, which on the bar means every
left-hand cue lights in a right-hand colour. Added a warning; a real fix is **Q-09**.
Tested both that it fires and that it does *not* false-positive on a real grand staff.

**B4 calibration mode (D-18) also done** — `firmware/src/fingerlight/calibration.py`.
Three anchors exist to make the two possible faults **distinguishable**: a mis-glued bar
(uniform offset → one `shift`) vs. a wrong M1 (error grows with position → re-measure;
a shift would only move the error to the other end). Fits `error_mm = a + b·position`.
Two real limitations are tested rather than hidden: a **~6.8 mm quantisation floor** (the
player can only press whole keys) and **end-anchor clamping** at A0/C8.

**Evidence**
```
python -m pytest firmware/tests score/tests -q  ->  113 passed
MusicXML -> DP -> lightscore -> firmware -> practice  ->  7/7 events, 0 errors, schema valid
python tools/check_vault.py                     ->  25/25 reachable, 0 broken
```

**My own test bugs caught en route:** three calibration tests failed first because I
injected the fault in *semitones* — which is a different distance in mm at each end of the
board — instead of a constant mm offset.

**Verified vs. assumed**
- VERIFIED: everything above, re-run by me rather than taken from the agent's report.
- ASSUMED: `<transpose>` handling (no fixture has a transposing instrument); cost weights
  still only ever met scales and one chord fixture, never a real piece.

**Next:** A2 (Q-09) is now the most valuable open item — A3 made its failure mode
concrete. Then A4 (OMR), which A3 unblocked.

## 2026-09-16 — A2, A4, B5, B6 delivered; upstream licence corrected

**Done — every remaining software item in both tracks.** Four parallel implementer
dispatches, then one fresh-context verifier that saw only artifact paths and acceptance
criteria, never the authors' reports.

- **A2 — Q-09 CLOSED.** `score/src/fingerlight_score/hands.py`. Three modes: `"staff"`
  (unchanged), `"pitch"` (split point by 1-D 2-means over distinct pitches), `"auto"`
  (**new default** — staff when the source really carried `<staff>`, pitch otherwise).
  `Note` gained `staff_is_explicit`; `musicxml.py` threads it per note. A 2-state Viterbi
  pass stops the hand flipping for a single note.
- **Cost tie-break.** New `COST_WEIGHTS["finger_spacing_mismatch"] = 3.0`, intra-chord
  only. Root triad C4/E4/G4 now fingers **1-3-5**, was 1-2-4.
- **A4 — the OMR arm.** `score/src/fingerlight_score/omr.py`: Audiveris subprocess wrapper
  + the post-OMR cleanup that matters (grand staff exported as two parts with no `<staff>`
  markup → one part with correct staff numbers) + a sanity report.
- **B5 — MIDI ingest.** `firmware/src/fingerlight/midi.py`. Pure streaming decoder
  (running status, note-on-vel-0 as note-off, split reads, injected clock) and BLE-MIDI
  packet framing per MMA/AMEI RP-052 §7/§9 — both in the testable layer. Guarded backends
  + replay backend. USB-preferred/BLE-fallback releases held notes on failover.
- **B6 — audio verification + benchmark.** `firmware/src/fingerlight/audio.py` (numpy
  only) and `tools/bench_audio.py`. Fundamental gate + harmonic corroboration, then
  subtract only the *predicted* harmonic contribution, so a real C3+C4 octave survives
  while an octave ghost does not.

**Evidence**
```
python -m pytest firmware/tests score/tests -q  ->  189 passed in 8.21s  (121 firmware, 68 score; was 113)
python tools/check_vault.py                     ->  27/27 reachable, 0 broken, 0 ambiguous
brute force 5**8, no DP pruning                 ->  both scales STILL the unique optimum, cost 20.0 unchanged
audio detection battery                         ->  18 cases, TP=44 FP=0 FN=0
OMR cleanup, with / without                     ->  9 notes & 3 on staff 2  /  6 notes & 0 on staff 2
```

**Verification was adversarial, by a fresh context that never saw the authors' reasoning.**
Verdict ALL PASS 10/10. It broke four implementations itself and confirmed a real test
failed each time, then reverted: note-on-vel-0 (3 tests failed), the hand-split direction
(3 failed, "bass in L" dropped 5→1), the audio fundamental gate (4 failed, FP 0→5), and the
OMR staff injection (3 failed). **All four reverts independently re-checked from disk
afterwards, plus a clean 189-pass run and a stray-file sweep** — an agent that edits source
and reverts is exactly where silent damage hides.

**Found — the docs said Piano-LED-Visualizer is GPL-3.0. It is MIT.**
Verified twice and independently: the pinned clone's `LICENSE` at `b502ad67` reads
`MIT License, Copyright (c) 2022 onlaj` with no GPL string anywhere in the repo, and an
**unauthenticated** `GET https://api.github.com/repos/onlaj/Piano-LED-Visualizer` returns
`license.spdx_id == "MIT"`. The claim had propagated into 7 files and was load-bearing:
**D-23** justified the separate PC-side compiler partly to keep "GPL-3.0 firmware
inheritance off the compiler", and **D-01** listed "wanting a non-GPL product" as a
reversal condition. Both reasons are void. The decisions themselves still stand on their
other grounds (D-15 put OMR offline; Audiveris needs a JVM and 2 GB+ heap), so nothing was
rebuilt — corrections were added in place with dated notes rather than rewriting the
decision log. Corrected in `README.md`, `HANDOFF.md`, `docs/01`, `docs/02`, `docs/03`,
`docs/04`, `docs/06`.

**Found — the Zero 2 W audio figure rested on a number from memory.** `bench_audio.py`
extrapolates with a 15–25× scaling factor read off remembered Geekbench scores, not a live
source. Rather than chase the factor, the claim was restated so it does not depend on it:
**the worst swept configuration (44100 Hz, 2048-sample frames, 10 candidates) has a PC
realtime factor of 0.0131, so the Pi would have to be ~76× slower than this PC before
realtime fails** — 3–5× of margin even if 15–25× is badly wrong. Recorded in
[06-implementation-plan](docs/index.md) and [HANDOFF](docs/index.md) §7.

**Found — two agents hit the same trap the other's work created.** Both A2's first
split-point design and A4's first default-output-path wrote or split wrongly in ways that
*looked* correct: A2's widest-gap heuristic mis-split the real grand staff because the left
hand's own internal C3→G3 gap (7 semitones) is wider than the true LH/RH boundary G3→C4
(5 semitones); A4's cleanup wrote a generated file into the read-only `fixtures/` dir. Both
were caught by their own regression tests before acceptance, not by review.

**Also:** `README.md` rewritten against the real tree (it still said Part 2 "not started",
M1 "waiting", and pointed at the long-gone `external/` clone). [HANDOFF](docs/index.md) §3, §4.5 and §7
updated; §4.5's "nobody has measured the piano yet" is resolved — M1 = 164.8 mm.

**Verified vs. assumed**
- VERIFIED: all five evidence lines above, re-run by the orchestrator rather than taken
  from any agent's report; the four sabotage-and-revert cycles; the MIT licence from two
  independent sources; `notemap.py:78-88` is a line-by-line verbatim transcription of
  upstream `lib/functions.py:528-545`; `score/src` imports nothing beyond stdlib +
  `defusedxml`, `firmware/src` nothing Pi-specific.
- ASSUMED / UNVERIFIABLE HERE, all labelled in their own docstrings: the Audiveris
  subprocess call and its output-filename guess (no JVM on this machine); the `mido` USB
  and `bleak` BLE adapters (no MIDI libraries, no adapter, no piano) — the BLE *framing* is
  tested, only the GATT plumbing is not; the Zero 2 W milliseconds; `BLACK_KEY_GAIN = 1.5`;
  palette readability in peripheral vision; M1 as a single unrepeated measurement.
- NOTED: the verifier's one WARN — with no git repo here, its "no stray files" check fell
  back to mtime comparison instead of a clean-tree diff. Weaker evidence, and another
  small argument for Step 0.1.

**Next:** Pi integration is now the whole remaining job — wire these pure modules to
upstream's `rpi_ws281x` driver, main loop and web UI on real hardware. Then the bar itself
(Kevin's CAD). Kevin's open calls: palette (B2), `git init` (0.1), and whether sustain
pedal (CC 64) should be handled — B5 drops it today because `practice.py` has no pedal
concept.

## 2026-09-16 — docs consolidated, 26 notes → 12

**Done.** Kevin asked for the whole documentation system simplified — "concise, simple,
easy to read, but has all the details at the same time". He picked the moderate option
(one note per Part) from three offered. Five merged notes written in parallel, each by an
agent given one bounded source set and a shared style contract; [README](README.md) and [index](docs/index.md)
written directly.

| Now | Absorbs | Words |
|---|---|---|
| [README](README.md) | old README, HANDOFF §1–4, the quick-reference table | ~900 |
| [index](docs/index.md) | old index, HANDOFF §5, research-index | ~450 |
| [hardware](docs/hardware.md) | 01-survey, 00-measurements, 07-dimension-spec, hardware-index, hardware/README | 3,967 |
| [firmware](docs/firmware.md) | firmware-index, audio-verification-benchmark, firmware/README, plan Track B | 2,382 |
| [score](docs/score.md) | score-index, lightscore-format, omr, score/README, lightscore/README, plan Track A | 2,305 |
| [decisions](docs/decisions.md) | 03-decision-log + every open question's current status | 1,852 |
| [provenance](docs/provenance.md) | 02-upstream-baseline, 04-scoreforge-merge + the 2026-09-16 verified findings | ~2,600 |

**29,694 words → ~17,000.** Research surveys kept untouched as an archive. 05-status-audit
was dropped: it was a point-in-time audit whose findings are already in this log.

**Old note names still resolve.** Each merged note carries the absorbed notes' names as
frontmatter `aliases`, so the **23 historical wikilinks in this log** — `[[HANDOFF]]`,
`[[01-mechanical-and-hardware-survey]]`, `[[firmware-index]]` and the rest — still work.
No log entry was rewritten to accommodate the restructure, which was the point.

**Evidence**
```
python tools/check_vault.py   ->  12 notes, 106 links, 0 broken, 0 ambiguous, 12/12 reachable
python -m pytest firmware/tests score/tests -q  ->  189 passed
mojibake sweep over all 12 notes                ->  0 files affected
fact-loss audit, backup vs live                 ->  87 absent tokens -> 63 after repairs
```

**Found — the fact-loss audit earned its keep.** Diffing every fact token (numbers with
units, D-xx/Q-xx IDs, file:line pointers, commit pins) between the pre-consolidation
backup and the live docs surfaced three real losses that no reviewer would have noticed:

1. **The per-file code maps had been compressed away.** 02-upstream-baseline's map of
   `lib/*.py` and 04-scoreforge-merge's `tab.py`/`pipeline.py` line anchors are precisely
   the working reference for the next job (wiring firmware to upstream). Restored in full
   as two tables in [provenance](docs/provenance.md).
2. **`firmware/config/settings_overlay.json` had zero mentions** anywhere in the new docs
   — its only documentation was the deleted `firmware/README.md`. It is the file that
   carries the generated `shift`/`note_offsets` into the firmware. Documented in
   [firmware](docs/firmware.md), with the warning not to hand-edit it.
3. **The ScoreForge archive's size and verification** (648 MB, 700 entries, CRC-verified,
   and the fact that the vendored copy has *no* git history) had been trimmed to a bare
   path. Restored in [README](README.md) — that zip is not a redundant backup.

The remaining 63 absent tokens are all accounted for: superseded 164.1 mm-era geometry,
stale test counts (113/95/68/20, now 189/121/68), the deleted notes' own filenames,
heading numbers, and tokenizer artifacts.

**Found — a benchmark figure I published was quoted too precisely.** Two notes disagreed
on the worst-config audio realtime factor, 0.0131 vs 0.0124. Neither was wrong: four runs
of the same configuration give 0.577–0.608 ms/frame, a ±5% spread from scheduling noise.
The honest statement is a range, and the conclusion to quote is the **~76× headroom**, not
the milliseconds. Corrected in [firmware](docs/firmware.md).

**Also corrected:** D-23's reversal condition still read "folding the compiler in would
make it a derivative work" — a GPL-era phrase, void under MIT. Now states the real
reversal condition (a Pi that could run Audiveris).

**Verified vs. assumed**
- VERIFIED: every evidence line above, run by the orchestrator; the 23 historical links
  resolving through aliases; the three restored facts present on disk after repair; the
  benchmark variance, measured over four fresh runs.
- ASSUMED: that the 63 remaining absent tokens are all benign — each was classified by
  eye against its source context, not by a second independent pass.
- NOTE: two writer agents were killed mid-wrap-up by an account spend limit. Both had
  already written their files; the artifacts were checked directly rather than resuming
  the agents. `docs/hardware.md` is therefore the one note whose author never completed
  its own fact-coverage audit — the orchestrator's audit above covers it instead.

**Next:** unchanged — Pi integration, then the bar. See [README](README.md).

---

## 2026-09-21 — Part 4 started: the CAD in MuJoCo, the bar lit by the firmware

**Done**
- New `sim/`: `build_assets.py` (STEP → STL), `make_scene.py` (→ `sim/scene.xml`,
  `sim/assets/keymap.json`), `verify_scene.py` (13 checks), `preview.py` (renders /
  interactive viewer), `sim/README.md`. The scene, the STLs and the renders are all
  generated; `make_scene.py` is the source.
- Both CAD files land in one scene: the piano as `cabinet` + 88 key bodies named for the
  note each one sounds (`key_A0` … `key_C8`), the bar as the CAD's own 7 mm WS2812 module
  tiled 176× at the design 6.9444 mm pitch, with `led_000` … `led_175` addressable
  individually. 442 geoms, 91 meshes, 44 971 triangles.
- `preview.py --cue NOTE:HAND:FINGER` lights the bar through `fingerlight.notemap` and
  `fingerlight.colors` — the same two functions the Pi calls — so a wrong LED in a render
  is a wrong LED on the bar. Four cameras render to `sim/renders/`.

**Evidence**
```
python sim/build_assets.py --part piano  -> tessellate 23.6 s, 89 STLs, 44 971 triangles
python sim/build_assets.py --part strip  -> tessellate  0.7 s, led_chip + strip_module
python sim/make_scene.py                 -> 442 geoms, 91 meshes
python sim/verify_scene.py               -> 13 checks, 0 failed
python sim/verify_scene.py --canary      -> mesh placement FAIL, worst 1.0000 mm
python tools/check_vault.py              -> 13 notes, 122 links, 0 broken, 13/13 reachable
python -m pytest firmware/tests score/tests -q -> 189 passed
```

**Found / changed**
- **The LED-to-key offset splits in two, and only half of it is ours.** Measured against
  the CAD's own key tail centres, each note's primary LED sits −6.68 … +2.09 mm off, worst
  at E4. Decomposed: the map's own quantisation is |max| 3.40 mm — exactly the floor
  `data/led_map.csv` predicts, so the firmware map reproduces itself in the sim — and the
  rest (−4.48 … +0.46 mm, mean −2.13) is this CAD replica's geometry differing from the M1
  model. **Nothing in the map should be changed off these numbers**; the replica is not the
  instrument. `verify_scene.py` reports the two halves separately.
- **The replica's keys are not evenly pitched.** Every white key is exactly 22.604 mm wide,
  but left-edge pitch runs 23.4428 … 23.7672 mm (spread 0.324 mm), and the CAD's implied
  octave span is 165.07 mm against M1's measured 164.8 mm. The spread is identical at
  tessellation tolerances of 0.25, 0.05 and 0.01 mm, so it is in the CAD, not in the mesh.
- **Flat on the shelf puts the LEDs level with the black keys.** The flat surface behind
  the key tails is at z 98.00 mm (40 779 mm² of it); the strip laid on it tops out at
  100.10 mm, which is −0.67 mm relative to the black-key tops and +9.39 mm relative to the
  white ones. That is option A in [hardware](docs/hardware.md) with its known shadow problem; the 10–15 mm
  clearance option B wants needs the printed wedge.
- **The strip CAD is not the design bar.** The file is a 1008 mm part, 144 LEDs at a
  nominal 7.000 mm pitch; the design is 176 LEDs at 6.9444 mm over 1222.21 mm. The sim
  tiles the CAD module at the design pitch rather than using the file's length, and covers
  the keyboard; the physical part on order must be the 1222 mm one.
- **gmsh is the wrong tool for these two files** (recorded so nobody retries it): it needs
  `highestDimOnly=False` or it imports 6 faces out of 42 079 (the piano has exactly one
  closed solid, `Red_felt`, among 124 products), it drops the STEP product names on import,
  and meshing those 42 079 faces ran 17 minutes without finishing. cascadio (OpenCASCADE
  `BRepMesh`) does the piano in ~24 s and keeps `White_key_01` … `Black_key_36` as names.
- **MuJoCo does not place a mesh where its file says.** It re-centres vertices on the
  centre of mass *and* re-orients them onto the inertia frame, compensating with
  `geom_pos`/`geom_quat`. `make_scene.py` therefore compiles once with everything at the
  origin, measures where each mesh actually landed, and re-emits with the difference.
  Getting the first half wrong produced a 2429 mm keyboard; the second, every part 15–32 mm
  out. Both were caught by `verify_scene.py`, not by looking at the render.

**Verified vs. assumed**
- VERIFIED: every command above, run this session; placement exact to 0.0000 mm against the
  STL files; the canary proves the placement check can fail; the quantisation figure
  cross-checks against `data/led_map.csv`'s own `centre_err_mm` column (−3.39 … +3.40).
- ASSUMED: that the flat 98.00 mm surface behind the key tails is the one the bar would be
  taped to — it is the largest flat top in the 30 mm band behind the tails, but nobody has
  put a strip on the real instrument. The 4 mm setback is a placeholder, not a measurement.
- NOT CHECKED: the capacitive-touch clearance question ([decisions](docs/decisions.md) D-09) — the sim knows
  nothing about the touch panel, and placing the bar in it does not make it safe.

**Next:** hinge the 88 key bodies so a played note moves its key; then decide option A vs. B
for the bar height and re-render both. Pi integration ([README](README.md)) is unchanged and still
the larger job.

---

---

## 2026-09-21 — bar moved forward to the top board's rim

**Done**
- The bar no longer hangs off the key tail line. `make_scene.py` now anchors it on the
  **front rim of the top board** (`BAR_FROM_RIM_MM = 0`, was `STRIP_SETBACK_MM = 4`), which
  moves it 21.32 mm toward the player: LED centres from y 80.38 → 55.06 mm, module front
  edge flush with the rim at y 49.09 mm. Height is unchanged (board at z 98.00, LED tops at
  100.10). Re-rendered all four cameras.
- `find_shelf` now also returns the rim; `verify_scene.py` gained two checks in place of
  the old "bar behind key tails", which the move made the wrong question.

**Evidence**
```
python sim/make_scene.py    -> board z=98.00 mm, front rim y=49.09 mm, 21.32 mm in front
                               of the key tails; bar 176 LEDs, y 55.06, top z 100.10
python sim/verify_scene.py  -> 14 checks, 0 failed
   bar on the board     bar front y 49.09 mm, board rim y 49.09 mm (overhang +0.00)
   bar clears the keys  664 key triangles cross the footprint; highest 88.15 mm (key_D3)
                        under a bar at 98.00 mm -> clearance +9.85 mm
python sim/verify_scene.py --canary -> 3 checks FAIL (placement 1.0000 and 20.0000 mm,
                                       clearance -5.56 mm)
python sim/preview.py --cue "C4:R:1,E4:R:2,G4:R:3,C3:L:5" -> 8 LED geoms lit of 176
```

**Found / changed**
- **The board overhangs the key tails by 21.32 mm** (rim y 49.09 vs tails y 70.40) — the
  keys run *under* it. Anchoring the bar on the tails had therefore parked it in the middle
  of the board; the rim is the forward limit, and the bar is now against it.
- **The first clearance check could not have failed.** It looked for key *vertices* inside
  the bar's 12 mm y-band, but these key meshes are boxes whose vertices sit only at the
  corners, so it examined **nothing** across all 88 keys and reported a clearance of
  +1e9 mm as a PASS. Rewritten to test triangles that *span* the band — 664 of them — and
  the `--canary` now lifts C#4 by 20 mm into the bar to prove the check reacts (it reports
  −5.56 mm).
- Clearance with the bar at the rim is **+9.85 mm**; the tallest key geometry under the
  footprint is D3's rear ramp at 88.15 mm, not a black key. Nothing collides.

**Verified vs. assumed**
- VERIFIED: every command above, this session; the rim is re-derived independently in
  `verify_scene.py` from the compiled cabinet mesh (49.09 mm both ways).
- ASSUMED: that flush with the rim is where the bar should go. It is as far forward as the
  board allows, but no measurement on the real instrument backs it, and the capacitive
  touch constraint ([decisions](docs/decisions.md) D-09) is still unmodelled — the rim is exactly where the
  touch surface begins, so this position is the one most in need of a real-hardware check.

**Next:** unchanged — hinge the key bodies; decide option A vs. B for bar height.

---

---

## 2026-09-21 — strip centred on the keyboard; 176 LEDs; the map no longer agrees

**Done**
- `make_scene.py` gained `--align {centre,map}` (default `centre`). Centred fills the
  keyboard edge to edge: **176 LEDs** at the fixed 6.9444 mm pitch span 1222.22 mm of a
  1226.21 mm playing width, leaving **+2.00 mm of margin at each end**. 176 is the most
  that fit — 177 would overhang by 2.95 mm. LED centres now run x −607.68 … +607.59
  (were −617.43 … +597.85, visibly left-shifted).
- `--align map` reproduces the previous placement: the origin `tools/gen_map.py` chose,
  −7.75 mm from A0's left edge, which leaves 11.74 mm bare at the top end.
- `verify_scene.py`: "LED 0 origin" replaced by `strip fills the keyboard` and
  `strip centred`; new `map matches strip origin` couples the layout to the shipped map.

**Evidence**
```
python sim/make_scene.py   -> align 'centre': 176 LEDs span 1222.22 mm of a 1226.21 mm
                              keyboard, margins +2.00 / +2.00; 177 would overhang 2.95 mm
                              tape starts +2.00 mm from A0's left edge; led_map.csv
                              assumes -7.75 mm -> 9.75 mm = 1.40 LED pitches
python sim/verify_scene.py -> 15 checks, 1 failed (map matches strip origin)
   strip fills the keyboard  176 LEDs, 176 is the most that fit
   strip centred             margins +2.00 / +2.00 (difference -0.000 mm)
   map matches strip origin  FAIL 9.82 mm = 1.41 pitches out; per-note LED-to-tail
                             error then |max| 13.14 mm
```

**Found / changed**
- **Centring the strip and `data/led_map.csv` are incompatible as things stand.** gen_map
  picks its origin by minimising worst-case centre error, not for symmetry; that lands the
  tape 7.75 mm left of A0 and 11.74 mm short of C8 — the left-shift visible in the renders.
  Centre the tape without regenerating the map and each note's LED sits up to **13.14 mm**
  from its key (about half a white key). The failing check names both remedies.
- **Regenerating the map for a centred tape is nearly free.** Re-running gen_map's own
  index/error computation at M1 geometry with the origin forced to the centred value
  (+1.003 mm in design space) gives **worst centre error 3.45 mm vs 3.40 mm**, still
  **88/88 unique LEDs**, indices 0..174. It renumbers every note, so it touches
  `data/led_map.{csv,json}` and `firmware/config/settings_overlay.json` — **not done, it
  is Kevin's call.**
- **A free middle option exists.** The error pattern is periodic in the LED pitch, so
  sliding the tape a whole number of pitches only renumbers LEDs. One pitch right of
  gen_map's origin (−0.81 mm from A0) gives margins −0.81 / +2.81 mm at design geometry —
  near-symmetric — with the worst-case error unchanged at 3.40 mm and `shift` in the
  firmware settings absorbing the renumbering.

**Verified vs. assumed**
- VERIFIED: every figure above, this session; the 176/177 fit, the margins, and the
  origin mismatch are each re-derived independently in `verify_scene.py` from the compiled
  scene and `data/led_map.csv`.
- ASSUMED: nothing new. The alignment options were compared using gen_map's own functions
  at M1 geometry, not at the CAD replica's (the instrument, not the replica, is what the
  map must fit).

**Next:** decide the alignment (centred + regenerate map / one-pitch slide / leave as
designed), then hinge the key bodies.

---

---

## 2026-09-21 — map regenerated for the centred tape; the sim plays a score

**Decisions (Kevin, this session):** keep the strip centred on the keyboard, and regenerate
the note→LED map to match rather than move the strip back.

**Done**
- `tools/gen_map.py` gained `--origin centre|<mm>` (default is still the min-error sweep).
  Regenerated at M1: `python tools/gen_map.py 164.8 --origin centre` →
  **origin +1.003 mm** from A0's left edge (margins +1.00 / +1.01 mm), **worst centre error
  3.45 mm** (was 3.40), **88/88 unique LEDs**, `shift = -2`,
  `note_offsets = [[21,-1],[51,1],[96,1],[107,-1]]`.
- Propagated to `data/led_map.{csv,json}`, `firmware/config/settings_overlay.json` and
  `StripSettings`'s defaults in `firmware/src/fingerlight/notemap.py`; updated the one test
  that pins the emitted settings-XML fragment.
- New `sim/play.py`: plays a light-score against the scene in a live MuJoCo viewer. The
  score is parsed by `fingerlight.lightscore`, the cursor is a real `PracticeSession`, and
  every frame drawn is that session's own `frame().pixels`. `--speed`, `--loop`,
  `--preview`, `--palette`, `--miss N` (fumble every Nth event), `--selftest` (headless,
  asserted) and `--frames DIR --fps F` (offscreen PNG sequence).
- `verify_scene.py`'s map check is now discrete: for every note, is the LED the map names
  the one actually nearest that key on this strip?

**Evidence**
```
python tools/gen_map.py 164.8 --origin centre -> origin +1.003 mm, worst 3.45 mm,
                                                 88/88 unique, shift -2, 4 offsets
python -m pytest firmware/tests score/tests -q -> 189 passed
python sim/verify_scene.py                     -> 15 checks, 0 failed
   map matches strip   68/88 notes light the LED nearest their key, 20 one index off,
                       worst 1 (tape +2.00 mm, map implies +0.83 mm = 0.17 pitches)
python sim/make_scene.py --align map; verify    -> map matches strip FAIL, 0/88 exact,
                                                   worst 2  (so the check can fail)
python sim/play.py --selftest                   -> 8/8 events, 8 distinct lit-sets,
                                                   0 missing LEDs, SELFTEST PASS
python sim/play.py --selftest --score two-hand-chords --miss 2
                                                -> 4/4 events, 1 wrong note rejected
python sim/play.py --frames ... --fps 6          -> lit region travels 523 -> 625 px and
                                                   goes dark after the last event
```

**Found — `gen_map.py` had the PLV shift sign inverted, and nothing could catch it.**
Upstream computes `note_offset = Σ(offsets past the note) − shift; led = naive − note_offset`,
so with `d = naive − led` the relation is `shift = −d(21)`. `plv_note_offsets` returned
`+d(21)`. **Every map the tool had ever produced happened to give `d(21) = 0`**, where the
sign is invisible; the centred map is the first with `d(21) = 2`, and the firmware's own
`test_all_indices_inside_the_strip` caught it immediately (C8 → LED 178 on a 176-LED
strip). Fixed in the generator, not in the artifact, and re-rendered. The lesson is not the
sign: it is that a constant of 0 hides the operation that produces it.

**Verified vs. assumed**
- VERIFIED: every line above, run this session. The new map is checked against the firmware
  three ways — the 189-test suite (which includes settings↔map agreement for all 88 notes),
  the sim's own nearest-LED comparison, and a deliberate misalignment that makes that
  comparison fail.
- ASSUMED: unchanged — that the bar sits flush with the board's rim, and that M1 = 164.8 mm
  from a single measurement is the instrument's true octave span. The 20 notes that are one
  index off are the CAD replica's geometry, not a map defect: the replica is 1.98 mm wider
  than the M1 model.

**Next:** hinge the 88 key bodies so a played note depresses its key, then drive them from
the same playback clock.

---

---

## 2026-09-21 — the keys move: 88 hinges, and a sweep that presses each one

**Done**
- Every key body is now a hinge about a balance rail 8 mm in front of its own rear end,
  at its mid-height, with a position actuator (`j_key_C4` / `act_key_C4`). The angle is
  **per key**, not shared: whatever gives *that* key's front tip the same 10 mm travel —
  3.46° for the longest white key, 5.01° for the shortest black one (arms 165.5 and
  114.0 mm).
- New `sim/keysweep.py`: walks A0 → C8, presses each key and lights the LEDs that key owns
  via `notemap.note_to_leds` + `colors.pair_colors`. Live viewer by default, `--selftest`
  headless with per-note assertions, `--frames DIR` for an offscreen sequence, `--notes`
  for a subset.
- `verify_scene.py` gained a `key action` check (88 hinges + actuators, per-key travel).

**Evidence**
```
python sim/make_scene.py       -> 442 geoms, 91 meshes, 88 hinges, 88 actuators
                                  hinge 3.46..5.01 deg for a 10.0 mm dip
python sim/verify_scene.py     -> 16 checks, 0 failed
   key action  88 hinges + actuators, 3.46..5.01 deg, front-tip travel 9.96..9.98 mm
python sim/keysweep.py --selftest   (all 88 keys)
   front-tip dip   9.86 .. 9.96 mm (mean 9.92, design 10.0)
   lit LED to key  0.01 .. 3.53 mm (mean 1.55; limits 7.0 each / 3.0 mean)
   failures 0 -> KEYSWEEP PASS
step response (measured, sim/keysweep.py gains): press 97.2% of travel at 50 ms,
   100% at 100 ms; release 0.04% left at 100 ms, 0.00% at 200 ms
```

**Found / changed**
- **Gravity had to go, and for a reason worth recording.** Each key's centre of mass sits
  in front of its pivot, so weight torques it *into* its own travel rather than against a
  stop: with gravity on, every key sagged 0.023 rad — 4.32 mm at the tip — and never
  returned to rest, which the sweep reported as "did not return" on every note. A real key
  is held up by its return spring against the front rail. Nothing else in this scene has
  contacts or dynamics worth simulating, so the scene now sets `gravity="0 0 0"` and the
  actuator alone decides where a key sits. Modelling the spring instead would need
  `k > 47 N·m/rad` to hold the sag under 0.5 mm, and a correspondingly stiffer actuator and
  smaller timestep, for no visible gain.
- **The first tip-travel probe measured the wrong thing.** It took "the vertices within
  2 mm of the key's front" *on every sample*. Rotation slides a key back by ~0.4 mm, so a
  different subset of a boxy mesh falls inside that window, and the mean z jumps for a
  reason that has nothing to do with motion: C#4 read as a 6.45 mm dip while its hinge had
  in fact turned 97.7% of a travel worth 9.6 mm. The probe now picks the tip vertices
  **once, at rest**, and follows those same ones. This is the same failure as the earlier
  clearance check — a geometric probe that re-selects its own sample set is not a
  measurement.
- **Actuator gains are measured, not guessed.** The first pair (kp=1, kv=0.04) reached only
  87.6% of travel in 100 ms, so a 180 ms hold under-pressed the key and the sweep's dip
  assertion failed honestly. kp=5, kv=0.08 gives 97.2% at 50 ms and 100% at 100 ms.
- **The sweep's LED check needed an aggregate to have teeth.** A per-note limit of 7 mm
  caught only 1 of 4 notes when the tape was deliberately moved 1.24 LED pitches
  (`make_scene.py --align map`). The mean offset over the sweep separates them cleanly:
  **1.55 mm aligned vs 5.33 mm misaligned**, so the sweep now fails on a mean above 3.0 mm
  and says "the map and the tape disagree, not one note".

**Verified vs. assumed**
- VERIFIED: every number above, measured this session from the stepped model; the sweep's
  assertions were shown to fail against a deliberately misaligned tape, and the scene's own
  `key action` check against the design travel.
- ASSUMED: the 10 mm front-tip travel and the pivot 8 mm in front of the key's rear end are
  plausible piano-action figures, not measurements of this instrument — nobody has put a
  depth gauge on Kevin's PX-S1100. The key masses (30 g white, 20 g black) are likewise
  nominal; with gravity off they only affect how fast a key moves, not where it ends up.

**Next:** drive the key actuators from `play.py`'s score clock so a played passage depresses
its keys as the bar cues them.

---

---

## 2026-09-21 — continuous weighted mapping + diffuser, prototyped in the sim only

**Kevin's ask:** he has bought a diffuser, so the bar will read as a continuous stripe
rather than dots. Treat the tape as continuous, divide it by the real key widths and
centres, and let width + centre decide how many LEDs light and how brightly. Unify the
colour for now so keys can be compared. Sigma chosen: **3.5 mm**.

**Done — nothing outside `sim/` changed.** No firmware, no `data/led_map.csv`, no settings
overlay. The prototype reads the geometry those are built from and proposes a different
drive, so the two can be compared before anything is committed.
- `sim/lightfield.py`: key slot ⊛ Gaussian(σ) sampled at the LED centres, normalised to a
  peak of 1; gamma 2.2 and 8-bit quantisation modelled; `field()` sums the driven LEDs'
  Gaussians to give the light the diffuser surface shows; `python sim/lightfield.py`
  prints the metrics table.
- Scene: a diffuser of **528 segments of 2.31 mm**, 8 mm above the LED tops (z 108.85 mm,
  +8.83 mm vs the black-key tops), coloured with the summed field rather than drawn as a
  translucent lid — MuJoCo has no light transport, so a lid would show dots under plastic.
- `sim/keysweep.py`: `--light pair|weighted`, `--diffuser`, `--chord "C4,E4,G4"`.

**Evidence**
```
python sim/lightfield.py
  mode             LEDs/key     FWHM  FWHM/slot   |centre|   crosstalk
  weighted s=2.0       2.38    11.2mm       0.80     1.65mm          0%
  weighted s=3.5       2.75    16.5mm       1.19     1.17mm          8%
  weighted s=5.0       3.24    22.8mm       1.65     1.89mm         32%
  fixed pair           2.00    15.2mm       1.10     6.86mm         21%
python sim/keysweep.py --selftest --light weighted --diffuser  -> 0 failures
python sim/verify_scene.py                                     -> 16 checks, 0 failed
python -m pytest firmware/tests score/tests -q                 -> 189 passed
LEDs per key at sigma 3.5: 2 -> 23 keys, 3 -> 64, 4 -> 1 (C8)
```

**Found / changed**
- **The weighted model is better on both axes at identical optics.** Worst apparent-centre
  error drops from **6.86 mm to 1.17 mm** (mean 3.50 → 0.29), *and* crosstalk drops from
  **21% to 8%** — because the fixed pair drives both pixels at full brightness while the
  weighted model dims the edge one. The improvement is not "more LEDs", it is being able
  to place the centroid between them.
- **Adjacent semitones merge at σ=3.5** (FWHM 16.5 mm vs a 13.73 mm slot pitch). With
  different finger colours they stay separable; with one unified colour they do not. See
  `sim/renders/semitone/`.
- **Two frame bugs, both caught by printed numbers rather than by looking.** (1) The
  diffuser's box geoms were written with absolute positions inside a body that had just
  been given its own frame, so they rendered at z 217.7 mm instead of 108.85 — box geoms
  are *not* re-centred by MuJoCo the way meshes are, so their `pos` is relative to the
  body. (2) The light field was being sampled at **world** x while the model's LED centres
  are in the **design** frame (x=0 at A0's left edge), putting the glow ~613 mm off the end
  of the bar; the diffuser rendered uniformly dark and looked like a no-op.
- **A `targetbody` camera aims at the body's frame origin.** `led_bar` and `diffuser` both
  had `pos="0 0 0"`, so every "look at the bar" camera was pointing at the floor. Both
  bodies now carry a real frame; the mesh two-pass absorbs the change automatically.
- **The unlit LED colour was brighter than the dimmest drive.** Unlit chips were rgba 0.07
  grey while a weight-0.28 pixel quantises to PWM 16 = 0.063, so the sweep's readback
  declared a genuinely lit LED dark. Unlit is now 0.03 and the threshold sits just above it.
- **`make_scene.py` was printing a stale constant.** It compared the tape's position against
  `STRIP_ORIGIN_MM = -7.75` and reported "9.75 mm out" when the regenerated map says
  +1.003 mm and the real difference is 0.99 mm. It now reads `data/led_map.json`.

**Verified vs. assumed**
- VERIFIED: every figure above, computed this session; the sweep's assertions pass in
  weighted+diffuser mode across the notes tested, and the scene's 16 checks still pass.
- ASSUMED: that a Gaussian of σ=3.5 mm is what this particular diffuser will do. It is
  derived from the 6–10 mm gap in [hardware](docs/hardware.md), not measured from the part Kevin bought;
  the real profile's scatter could be wider, narrower or non-Gaussian. Everything above is
  a *relative* comparison, which survives a wrong σ far better than any absolute claim.
- NOT DECIDED (and not the sim's to decide): the map format would become
  `note -> [(led, weight)]`, which upstream's `shift`/`note_offsets` cannot express
  ([decisions](docs/decisions.md) D-08 territory); and the hand/finger two-pixel encoding has to be replaced,
  because a blob cannot carry two roles.

**Next:** Kevin's call on σ and on the two open decisions above; then, if it goes ahead, the
firmware-side format change and a regenerated map.

---

---

## 2026-09-21 — narrower blob, a real channel, and the diffusing model corrected

**Kevin:** the lit width was too wide, the cap sat too far above the tape so the LEDs were
still visible, and "make sure the diffusing alg is correct".

**Done**
- **The drive no longer pre-blurs.** `weights()` is now the overlap of each LED's cell with
  a band centred on the key, normalised to a peak of 1 — the sharp band goes to the LEDs
  and the diffuser blurs it exactly once. The previous version sampled an already-blurred
  profile and let the optics blur it a second time.
- **Width knob separated from the optics:** `TARGET_SLOT_FRAC = 0.7`, i.e. the band is 70%
  of the key slot. FWHM **16.5 mm → 11.3 mm**, which is **0.82 of the 13.73 mm key** it
  marks (was 1.19).
- **The gap is now set by continuity, not taste.** `SIGMA_MM = 0.45 x DIFFUSER_GAP_MM`, and
  `ripple()` measures the undulation of the summed field with every LED driven equally.
- **The bar is a channel.** Two side walls from the tape up to the cap, cap 1.2 mm wider
  than the tape. The emitters are no longer visible under the cap's leading edge.
- `sim/lightfield.py` prints the whole comparison, including a control row.

**Evidence**
```
python sim/lightfield.py   (gap 7.0 mm -> sigma 3.15 mm, ripple 0.00%)
  drive              LEDs/key     FWHM  FWHM/slot   centre err      rms crosstalk
  band 70% of slot       1.90    11.3mm       0.82       0.68mm    0.080        1%
  band 85% of slot       2.20    12.9mm       0.92       1.21mm    0.064        2%
  band 100% of slot      2.47    14.7mm       1.06       1.33mm    0.060        3%
  today's fixed pair     2.00    14.4mm       1.04       6.85mm    0.251       16%
  (control: +1 LED)         -    11.3mm          -       7.32mm    0.371        -
ripple vs gap: 4mm 69%, 5mm 39%, 6mm 12%, 7mm 0.0%
python sim/keysweep.py --selftest --light weighted --diffuser  (all 88) -> 0 failures
python sim/verify_scene.py -> 16 checks, 0 failed
python -m pytest firmware/tests score/tests -q -> 189 passed
```

**Found / changed**
- **A 5 mm gap would have shipped visible dots.** My first answer to "too wide" was to drop
  the cap from 8 mm to 5 mm, which does narrow the blob — and leaves the summed field
  undulating by **39%**, i.e. you see the individual LEDs through the diffuser. Measuring
  the ripple killed that idea: continuity needs ≥ 7 mm, so the blob can only be narrowed
  through the **band**, never through the gap. The 6–10 mm figure in [hardware](docs/hardware.md) now has a
  derivation behind it instead of a citation.
- **The model was blurring twice.** Weights sampled from a blurred profile, then re-emitted
  as Gaussians, is `box ⊛ G ⊛ G`. Worth ~0.5 mm at σ=3.5 and more at larger σ, and wrong
  either way: the drive carries the band, the optics carry the blur.
- **`argmax` is the wrong centre for a flat-topped blob.** The fidelity check reported an
  8.38 mm "peak error" for blobs whose centre of mass was within 1.7 mm, because the
  argmax of a flat top jumps between LEDs. Replaced with the midpoint of the half-maximum
  span: worst error **0.68 mm** at the chosen setting.
- **The weight floor had drifted to a value the hardware cannot show.** 0.18 is PWM 6/255
  after gamma — dimmer than an unlit package renders — and the sweep's readback correctly
  called those LEDs dark, failing on G1 and E5. Restored to 0.25 (PWM 12) with the reason
  written next to it.
- **The rms and centre-error columns now carry a control row**: the same key driven one LED
  to the right scores rms 0.371 vs 0.080 and centre error 7.32 mm vs 0.68 mm. Without it
  those columns were just small numbers.

**Verified vs. assumed**
- VERIFIED: every figure above, computed this session; the sweep passes across all 88 keys
  in weighted+diffuser mode; the fidelity metrics were shown to move under a deliberately
  wrong drive.
- ASSUMED: `sigma = 0.45 x gap`. This is a modelling coefficient, not a measurement of the
  profile Kevin bought — it sets where the ripple threshold lands (7 mm here), so the real
  part could need more or less gap. One photograph of a single lit key through the real
  diffuser is enough to fit it, and every comparison above is relative, so the ranking
  survives a wrong coefficient even if the absolute gap does not.
- UNCHANGED: still nothing outside `sim/`. The map format and the hand/finger encoding
  decisions are still open.

**Next:** Kevin's verdict on the width; then, if it goes ahead, the firmware-side format.

---

---

## 2026-09-21 — docs brought up to date; Part 4 written into the vault

**Kevin:** keep the simulation as it stands (tuning waits for the hardware build); make the
documents current and write the simulation into them.

**Done**
- New note **[simulation](docs/simulation.md)** — Part 4 in the vault: what the scene contains, what it
  measured about the instrument, what it caught, the continuous-mapping prototype and its
  numbers, and an explicit VERIFIED / ASSUMED / NOT MODELLED section. `sim/README.md` stays
  as the runnable reference and the two link to each other.
- [index](docs/index.md) now lists seven notes; [README](README.md) describes four parts and carries the sim's
  check line.
- **Stale facts corrected**, all read from the artifacts rather than retyped:
  `strip origin -7.75 → +1.003 mm`, `worst centre err 3.4 → 3.45 mm`,
  `shift 0 → -2`, `note_offsets [[63,1]] → [[21,-1],[51,1],[96,1],[107,-1]]` in
  [hardware](docs/hardware.md) §2.1, [firmware](docs/firmware.md)'s comparison table and [README](README.md)'s quick reference.
- New [hardware](docs/hardware.md) §2.1a explains why the tape is centred and what it cost; §3 now records
  the board's 21.32 mm overhang and where the bar actually sits; §5's 6–10 mm diffuser gap
  has a derivation (the ripple sweep) instead of a citation.
- [decisions](docs/decisions.md): **D-25** (centred tape) and **D-26** (build a simulation) added; D-08's
  parenthetical corrected; **Q-10** (map format becomes note → [(led, weight)]?), **Q-11**
  (how to encode hand and finger in one blob), **Q-12** (measure the real diffuser's blur)
  opened.
- [README](README.md) "Your calls" gained the two decisions and the one measurement Kevin owns.

**Evidence**
```
python tools/check_vault.py -> 14 notes, 161 links, 0 broken, 0 ambiguous, 14/14 reachable
python -m pytest firmware/tests score/tests -q -> 189 passed
python sim/verify_scene.py -> 16 checks, 0 failed
```

**Verified vs. assumed**
- VERIFIED: every number written into the notes was printed from `data/led_map.json`,
  `firmware/config/settings_overlay.json`, `sim/assets/keymap.json` or `sim/lightfield.py`
  in this session — none was copied from an earlier draft or from memory.
- ASSUMED: nothing new. The simulation's own assumptions (10 mm key travel, nominal key
  masses, σ = 0.45 × gap) are now stated in [simulation](docs/simulation.md) rather than only in the log.

**Next:** the app — what the hardware's key functions are, what features it should carry,
and the touchscreen GUI. Discussion, not code, per Kevin.

---

---

## 2026-09-22 — platform change: Pi 5, DSI touchscreen, USB mic, USB-MIDI

**Kevin's decisions this session:** Raspberry Pi 5 + 5" DSI capacitive touchscreen
(800×480) instead of the Zero 2 W and its SPI LCD HAT; a USB microphone as a **backup**
input for instruments with no MIDI out; USB-MIDI from the piano as the real input; and the
app is a **web app**, not a native GUI.

**Done — documents only, no code touched.**
- [decisions](docs/decisions.md): **D-27** (platform), **D-28** (LED data moves to SPI0/GPIO10, driven by
  SPI-encoded bytes rather than `rpi_ws281x`), **D-29** (the mic is a backup, MIDI is the
  truth), **D-30** (kiosk web app). **Q-07 CLOSED** after being open since the first
  session. **D-10 struck through** — superseded by D-27/D-28. D-23 annotated: its reversal
  condition (a Pi that can host a JVM for Audiveris) is now reachable but **not acted on**.
- [hardware](docs/hardware.md): new **§6.1a** on the Pi 5 LED-driver problem and the three paths; §6 retitled
  to the Pi 5 with the Zero 2 W analysis kept as the reason the pin is where it is; §6.2
  rewritten (one cable, no OTG adapter, no hub); §6.3 now separates what the Pi 5 changes
  from the Zero 2 W history; power table and supply line updated; **BOM re-costed
  ≈125–155 → ≈195–255 USD**; the wiring-diagram line now says the diagrams still show
  GPIO18 and need redrawing.
- [firmware](docs/firmware.md): the "no `rpi_ws281x`" framing corrected; the unverified list rewritten — the
  USB MIDI adapter is now the *primary* path, BLE drops to optional, and the audio
  benchmark is noted as assuming a line-in rather than this mic.
- [provenance](docs/provenance.md): `rpi_ws281x` strip driver and the LCD menu marked **DROPPED** with reasons;
  the Flask row now says the app is ours and upstream's routes are a starting point.
- [README](README.md): four parts line, quick-reference rows (controller, LED pin, inputs, BOM), and
  the "written but unverified" paragraph now leads with the new unknown.

**Evidence**
```
python tools/check_vault.py -> 14 notes, 0 broken, 0 ambiguous, 14/14 reachable
python -m pytest firmware/tests score/tests -q -> 189 passed
python sim/verify_scene.py -> 16 checks, 0 failed
```

**Found / changed**
- **`rpi_ws281x` cannot drive a Pi 5.** It DMAs into BCM2835 PWM/PCM peripherals; the Pi 5
  puts GPIO behind RP1. This invalidates the stock wiring (D-10) and a chunk of what D-01
  counted as "60% of the work already done" — the strip driver is now ours to choose.
- **The escape hatch was already written down.** D-10's reversal clause reads "dropping the
  LCD HAT and the hub → move LEDs to SPI0/GPIO10", and §6.1 option A had already done the
  arithmetic: 176 LEDs ≈ 1.6 kB per SPI frame, inside the stock 4096-byte `spidev` limit.
  Two of option A's caveats evaporate on a Pi 5 — `core_freq` no longer gates the SPI clock,
  and the I²S-vs-PWM conflict is moot now that audio comes in over USB.
- **A Pi 5 needs its own supply.** 5 V/5 A USB-C PD, separate from the LED brick, grounds
  commoned at one point. A Pi browning out mid-passage is indistinguishable from a firmware
  bug.
- **The mic has a blind spot worth stating in the docs:** with headphones on — which is how
  a digital piano is usually practised — it hears nothing. That is why it is a backup for
  no-MIDI instruments (D-29) and not a verification channel that runs alongside MIDI.

**Verified vs. assumed**
- VERIFIED: the vault is consistent (0 broken links, 14/14 reachable); tests and sim checks
  still pass; every superseded value was struck through rather than deleted.
- **ASSUMED, and flagged as such in [hardware](docs/hardware.md) §6.1a:** that `rpi_ws281x` fails on a Pi 5
  and that the SPI path works there. Both are *reported*, neither measured on Kevin's board.
  The first bench task when the Pi 5 arrives is 176 LEDs over SPI, watching for flicker and
  for the far end lagging. Fallback order: RP1 PIO, then a Pico.
- ASSUMED: the 5" panel ships with the Pi 5 22-pin FPC cable — the 4B cable does not fit.
  Worth checking before the box is opened and the cable is the only missing part.

**Next:** the app itself — screens and flows for an 800×480 touch panel, and which of them
the firmware can actually serve today.

---
---

## 2026-09-22 — Part 5: the touchscreen app, drawn at 800x480

**Done**
- `app/ui/` — four screens as real HTML/CSS at exactly 800x480, rendered headless and
  checked: **Play**, **Library**, **Session**, **Settings**. No CDN, no webfont, no
  framework; the Pi will sometimes be offline beside the piano. Renders committed as
  `app/ui/shot_*.png`.
- New vault note **[app](docs/app.md)** (Part 5): the rule the design follows, what 800x480 on a 5"
  panel actually affords, the four screens, the flow between them, and a table of what the
  firmware can already serve.
- [index](docs/index.md) and [README](README.md) updated: eight notes, five parts, `app/ui/` in the layout.

**Evidence**
```
headless Chromium 800x480 -> app/ui/shot_{play,library,session,settings}.png
python tools/check_vault.py -> 15 notes, 193 links, 0 broken, 15/15 reachable
python -m pytest firmware/tests score/tests -q -> 189 passed
```

**Found / changed**
- **The panel's arithmetic decides the layouts, not taste.** 933 px over a 5 in diagonal is
  187 ppi, so 1 mm = 7.35 px: a comfortable touch target is 66 px, the floor is 52 px.
  After a 56 px top bar and gutters there is ~400 px left, which is *one* row of controls
  plus one content block, or four list rows. The library therefore shows four scores and
  the fifth row became a scroll affordance rather than a clipped row.
- **Drawing the screens exposed what the firmware cannot do yet.** Play is roughly
  buildable today (`frame()`, `waiting_for`, `progress()`, `errors`, `wrong_notes` all
  exist); **Session, loop A-B and tempo scaling do not exist at all**, and neither do bar
  numbers — the light-score carries onsets in milliseconds, while every useful UI label
  ("loop bars 9-16", "bar 11") is in bars. Either the compiler emits bar/beat alongside
  `t`, or the app derives them from the tempo map the schema already carries.
- **Play-along mode is in the UI and not in the state machine.** The session advances on
  note events, not against a clock; wait-for-me is what exists.
- The UI uses the firmware's own `PALETTE_RAMP` values, so a chip on screen and an LED on
  the bar are the same hue rather than two people's idea of "amber".

**Verified vs. assumed**
- VERIFIED: each screen was rendered at 800x480 and inspected; two layout bugs found that
  way and fixed (button labels overflowing their boxes, the fifth library row clipping).
- ASSUMED: that 66 px is comfortable on this particular panel. The ppi arithmetic is sound
  but nobody has touched the thing yet; check it on the real screen before the layouts
  harden.
- NOT DONE: no wiring to the firmware, no server, no state. These are mockups that happen
  to be the front-end skeleton.

**Next:** Kevin's call on play-along vs wait-for-me as the default, and whether bar numbers
come from the compiler or from the app.

---
---

## 2026-09-22 — build-day kit: runbook, hardware tests, Pi setup

**Kevin is building the bar today** on a Pi 5 with a real strip. This is the routing and
the code for that, not new design.

**Done**
- **[build](docs/build.md)** — the runbook: 176 LEDs, data on GPIO10, and six stages where every step
  has a test and a pass criterion, plus a symptom table (nothing lights / first LED only /
  red shows green / wrong count / far end pink) that maps each failure to its cause.
  Routed from [index](docs/index.md) and [README](README.md) so build day starts in one place.
- `tools/hw/ws2812_spi.py` — the Pi 5 LED driver: WS2812 bits as 4 SPI bits at 3.2 MHz
  (`1000` / `1110`), GRB order, brightness, a 60 us latch. `--selftest` checks the encoder
  against hand-derived byte patterns **with no hardware**.
- `tools/hw/led_test.py` — staged bring-up (`first walk count order current map sweep`),
  every stage printing what to LOOK FOR; `--dry-run` rehearses any stage off the Pi.
  The `map`/`sweep` stages go through the real firmware map, not a copy of it.
- `tools/hw/midi_test.py` (`ports/watch/latency`), `tools/hw/mic_test.py`
  (`devices/level/note`), `tools/hw/preflight.py` (board, SPI, buffer, groups, packages,
  data files, frame size), `tools/hw/setup_pi.sh` (dry-run by default, `--apply` to act).

**Evidence**
```
python tools/hw/ws2812_spi.py --selftest -> SELFTEST PASS
   176 LEDs, full white -> 2136 bytes (spidev default limit 4096)
python tools/hw/led_test.py <stage> --dry-run  (all six)
   first 2 frames / walk 178 / count 2 / order 5 / current 3 / sweep 90
python tools/hw/led_test.py map C4 E4 G4 --dry-run
   C4 -> LED 78+79, E4 -> 86+87, G4 -> 92+93   (through fingerlight.notemap)
python tools/hw/preflight.py   -> runs off-Pi, skips the Pi-only checks, and
   correctly FAILs the four missing packages on this machine
bash -n tools/hw/setup_pi.sh   -> syntax ok
python tools/check_vault.py    -> 16 notes, 217 links, 0 broken, 16/16 reachable
python -m pytest firmware/tests score/tests -q -> 189 passed
```

**Found / changed**
- **The frame is 2136 bytes**, not the 1.6 kB the old option-A note estimated — the
  difference is the 24-byte latch and rounding, and it is still comfortably inside
  spidev's stock 4096. Worth knowing because a library that spells a WS2812 bit with
  *eight* SPI bits instead of four needs 4224 and would fail on a stock buffer. `setup_pi.sh`
  raises `spidev.bufsiz` anyway.
- **Two of my own checks reported nothing and looked fine**, both caught here rather than
  on the bench: a garbled assertion in the encoder selftest compared a string to itself,
  and the dry-run frame counter read the SPI handle *after* `close()` had dropped it, so it
  printed "0 frames" for a run that sent 178.
- The runbook quotes **LED 0's centre at 4.475 mm** right of A0's left edge, printed from
  `data/led_map.json` rather than rounded by hand.

**Verified vs. assumed**
- VERIFIED: the encoder byte patterns, the frame size, every stage's frame count, the
  note→LED numbers, that each script fails with an actionable message when its library is
  missing, and that the vault still resolves.
- **ASSUMED — the whole point of stage 3:** that a Pi 5 can drive 176 WS2812s over SPI at
  all. Nothing in this session touched hardware. The encoder being verified means a failure
  narrows to wiring, timing or the layer beneath; the fallback order is RP1 PIO, then a Pico.
- ASSUMED: `1110` for a logical 1 (937 ns T1H) is accepted by these parts. `--one-pattern
  1100` drops it to 625 ns if a strip is fussy — one flag, no code change.

**Next:** the bench. Then a photo of one lit key through the diffuser, which is what closes
Q-12 and turns the simulation's blur coefficient from an assumption into a measurement.

---

## 2026-09-30 — Part 5 built: the app, wired; Pi flashing and kiosk runbook

**Done**
- **The app, end to end** (`app/`): a stdlib HTTP + server-sent-events server, an engine
  that drives `PracticeSession` from MIDI / mic / on-screen keys and pushes every frame to
  the LED sink, and the four screens as one vanilla-JS page that scales from 800×480 to any
  panel. Library, Play, Session and Settings all work; loops, play-along, tempo and attempt
  history live in the engine, not the state machine — [app](docs/app.md) §5 update, §7.
- **PC preview** at `/preview`: the live app in an 800×480 frame plus the 176-LED bar and
  88 keys drawn to scale from `data/led_map.json`, playable by mouse or computer keyboard.
- **[pi-setup](docs/pi-setup.md)**: Imager → first boot → either panel → copy code → `setup_pi.sh` →
  `install_app.sh` (systemd service + labwc kiosk) → scores → troubleshooting.
- `tools/compile_score.py`: MusicXML/.mxl → light-score, validated by the firmware reader.
- Firmware: `PracticeSession.seek()` (+1 test) and `midi.pick_input_port()` (+1 test).

**Evidence**
```
python -m pytest firmware/tests score/tests app/tests -q -> 218 passed (123 + 68 + 27)
python tools/check_vault.py -> 17 notes, 0 broken, 17/17 reachable
Chromium over CDP, 7 renders (app/shots/): 0 elements outside the panel (every screen at
  800x480, Play also at 1280x720), 0 buttons under 52 px, 0 mojibake, 0 console errors
bash -n + dry runs: setup_pi.sh, install_app.sh, kiosk.sh
```

**Found / changed**
- **`setup_pi.sh` would have failed on today's Pi OS.** The current release is Trixie
  (Debian 13), which has **no `python3-sounddevice`** — and one missing name fails the whole
  `apt-get install`, so the LED and MIDI packages would not have installed either. Now:
  `libportaudio2` from apt, `sounddevice` via `pip3 install --user --break-system-packages`.
  `preflight.py` and `mic_test.py` give the same fix.
- **Both MIDI port pickers opened ALSA's "Midi Through" loopback.** On a Pi it is always
  listed first, so `UsbMidiBackend()` and `midi_test.py watch` would have connected to a
  port nothing plays into. Both now skip it.
- **The official 5" Touch Display 2 is 720×1280 portrait**, not 800×480; the BOM row does
  not name a model. The app now scales instead of assuming the panel, and [pi-setup](docs/pi-setup.md) §0
  covers both kinds.
- The mockups' 36 px tabs broke [app](docs/app.md) §2's own 52 px floor; the live app uses 52 px. Two
  layout probes earned their keep: the overflow probe caught an 859 px-wide Play screen,
  and the request counter showed a "Play" render that was really the Library (hash-only
  navigation, no `hashchange` handler).

**Verified vs. assumed**
- VERIFIED on the PC: the above. ASSUMED, from primary sources only: Trixie package names,
  both panels' setup lines, Imager's customisation tab, the blanking command.
- **UNVERIFIED — needs the Pi:** the service under systemd, the labwc autostart (including
  whether a user autostart replaces the system one — the installer seeds it to be safe),
  kiosk Chromium, MIDI from the PX-S1100, the mic, and SPI to the strip.

**Next:** flash the card ([pi-setup](docs/pi-setup.md)), then the bench ([build](docs/build.md) §3).

---

## 2026-10-29 — Bring-up tools: smoke gate, free play, per-key calibration

Kevin reports the hardware built, the Pi set up and the touchscreen UI running. This
session builds the next three test objectives; ScoreForge (song → MIDI / playable file)
will live in its own repo and connect later.

**Done**
- **Objective 0 — `tools/hw/smoke.py`**: one guided run — preflight, three strip patterns
  with yes/no questions, then all 88 keys lit through the real map as they are played.
  Reports dead / stuck / out-of-range keys, velocity range, whether the pedal arrived and
  the Pi-side note → LED time; writes `~/.fingerlight/smoke-report.json` and a block for
  this log. [build](docs/build.md) "The short route".
- **Objective 1 — Free play** (`firmware/src/fingerlight/freeplay.py`, new tab): the bar
  lights whatever is played whenever no attempt is live. Styles solid / split / rainbow /
  velocity, ten swatches, split key, release fade, sustain pedal hold. [app](docs/app.md) §9.
- **Sustain pedal**: `MidiDecoder(pedal=True)` emits `PedalEvent` edges for CC 64; the
  default decoder is unchanged, and practice still ignores the pedal.
- **Objective 2 — per-key calibration** (Settings → *Calibrate keys…*): select a key by
  playing it, nudge its pair one LED at a time, shift all, reset; *Save as default* writes
  `~/.fingerlight/led_offsets.json`, loaded on every start. Applied in `note_to_leds` on top
  of the unchanged upstream formula. [app](docs/app.md) §9.

**Evidence**
```
python -m pytest firmware/tests score/tests app/tests tools/tests -q
  -> 261 passed (143 firmware, 68 score, 45 app, 5 tools)
python tools/hw/smoke.py --dry-run --yes y   -> runs end to end off a Pi (preflight FAILs
  for spidev/mido, as it should on a PC); the key step is tested against a fake mido
Chromium at 800x480: Free play (4 styles, with/without a score open), Settings, Calibrate
  -> 0 elements outside the panel, 0 buttons under 52 px (bar the 88 overview cells),
  0 console errors; /preview bar lights, holds under Shift-as-pedal, fades to dark
```

**Found / changed**
- **A stopped attempt could restart itself.** After *Stop*, the next key press in wait mode
  called `transport("play")` on the summarised attempt, and a second Stop wrote a second
  history row. Attempts now carry `ended`; an ended attempt ignores keys and transport.
- **"Shift all →" could never work with a strict pair check**: the centred tape puts C8 on
  LED 174 and A0 on LED 0. Only the key's own LED must stay on the strip; its partner is
  already clamped.
- A quadratic fade computed from float times never reaches exactly 0 (1.4 − 1.0 ≠ 0.4), so a
  key could "fade" forever at an invisible level; anything under one 8-bit step is dark.

**Verified vs. assumed**
- VERIFIED on the PC: the above.
- **UNVERIFIED — needs the Pi:** all of it on hardware — the smoke run itself, pedal CC 64
  from the PX-S1100 (`midi_test.py watch` shows it), free-play latency by eye, and whether
  one-LED calibration steps are fine enough through the diffuser.

**Next:** run `tools/hw/smoke.py` on the Pi and paste its block here; calibrate; then
objective 3 (play a song from the Pi through the piano) once ScoreForge emits MIDI.

---

## 2026-10-29 — Doc links work on GitHub

**Done**
- Every Obsidian `[[wikilink]]` in the docs (224 across 12 files) is now a relative
  Markdown link, `[hardware](docs/hardware.md)`. GitHub rendered the wikilinks as literal
  `[[hardware]]` text, so no cross-reference in the repo was clickable there. Old note names
  that are now aliases (`[HANDOFF]`, `[01-mechanical-and-hardware-survey]`, …) keep their
  wording in this log and point to the notes they were merged into.
- `tools/check_vault.py` now checks relative links (to notes and to files), and fails on
  any wikilink left outside code, as well as on broken links and unreachable notes.
- `.obsidian/app.json`: new links are written as relative Markdown links, not wikilinks.

**Evidence**
```
python tools/check_vault.py -> 17 notes, 252 relative links, 0 broken, 0 wikilinks,
  17/17 reachable; a planted [x](nope.md) and [[hardware]] each fail it (exit 1)
```

**Next:** unchanged — the smoke run on the Pi.

---

## 2026-10-30 — Free play feels like a piano; dimmer default

Kevin: the smoke test passed on the Pi (and `led_mirror` went in on main for a bar fed from
the C8 end). Feedback from the first real use: key presses should fade, the bar is too
bright, and the pedal should do something like lengthen the fade.

**Done**
- **Strike decay** (`Look.strike`): a key lights at full and eases over 1.2 s to a glow
  while held — *soft fade* 40 % (default), *flash* 12 %, or *steady*. Release fades from
  that glow.
- **Pedal lengthens fades** (`Look.pedal = "longer"`, default): under the pedal a release
  fades 4× slower (≥ 1.5 s); lifting it re-times every tail still glowing to the normal
  fade from its current level — no jump. *hold* (the old behaviour) and *pedal off* remain.
- **Brightness default 40 % → 25 %**, with a one-time `settings.json` migration (v2) that
  moves a brightness still at the old default and maps `fp_sustain` to `fp_pedal`.
- Free play screen: a *Key press* row; *Release · pedal* row with fade 0 / 0.4 / 0.8 /
  1.5 s (0.4 s, the default, is now one of the choices) and pedal off / longer / hold.

**Evidence**
```
python -m pytest firmware/tests score/tests app/tests tools/tests -q
  -> 271 passed (147 firmware, 68 score, 48 app, 8 tools)
Chromium at 800x480: Free play in every style -> 0 elements outside the panel, 0 buttons
  under 52 px, 0 console errors
```

**Found / changed**
- **A settled glow could be left one step short.** The engine only redrew a held key while
  it judged the key "still animating"; a tick landing just after the decay ended left the
  last in-between level on the strip. The tick now redraws whenever anything is lit;
  `_render_leds` already drops a frame identical to the last, so a settled bar costs
  nothing.

**Verified vs. assumed**
- VERIFIED on the PC: the above. **UNVERIFIED on the Pi:** how 40 % / 12 % glows and a
  25 % cap actually look through the diffuser — all four numbers are first guesses.

**Next:** play it on the bar and tune `STRIKES`, `DECAY_MS`, `PEDAL_FADE_X` by eye.

---

## Entry template

```markdown
## YYYY-MM-DD — short title

**Done**
- …

**Evidence**
- command run → key output line

**Found / changed**
- …

**Verified vs. assumed**
- …

**Next:** …
```
