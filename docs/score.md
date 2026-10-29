---
name: score
description: Part 3, the offline compiler that turns MIDI/MusicXML/OMR input into a device-independent light-score
date: 2026-09-16
aliases: [Score, score-index, lightscore-format, omr, Score pipeline]
tags: [score]
---

# Score pipeline (Part 3)

The score compiler runs on the PC, never the Pi (D-15, D-23), and turns a score into a
[light-score](#a0--light-score-format-q-05-closed) — the file the firmware plays back.
Code: `score/src/fingerlight_score/`. Prior art: `scoreforge/` (vendored), mapped by a
now-merged note. **68 score tests pass on the PC** (VERIFIED, `python -m pytest
score/tests -q`, 2026-09-16).

```
score/src/fingerlight_score/   the compiler
score/tests/                   pure logic, synthetic notes, no audio needed
```

Four input arms converge on one note/event model (`notes.py`), then one fingering DP,
then one emitter:

| Arm | Status |
|---|---|
| MIDI file | ScoreForge has it |
| audio (record yourself) | ScoreForge has it end to end (D-24) |
| MusicXML | **A3 done** |
| PDF / photo via OMR | **A4 done for cleanup, subprocess call UNVERIFIED** |

## Track A summary

| | Work | State |
|---|---|---|
| A0 | Light-score format (Q-05) | ✅ done, fixtures validate, negative control rejects |
| A1 | Fingering DP | ✅ done |
| A2 | Hand assignment (Q-09) | ✅ done |
| A3 | MusicXML ingest | ✅ done |
| A4 | OMR arm (Q-08) | ✅ done for cleanup; ⚠️ subprocess call UNVERIFIED |

## A0 — light-score format (Q-05) CLOSED

JSON v1. Schema: `lightscore/schema/lightscore-v1.schema.json`. Fixtures:
`lightscore/examples/`. **Device-independent: LED indices are deliberately not
stored** — the firmware derives them from its own calibrated note map, so one
light-score plays on any bar (this is the format's whole reason to exist).

Top-level: `format` (const `"lightscore"`), `version` (const `1`), `notes` (required
array, sorted by onset `t` ascending — "short keys are deliberate: a dense piece is
tens of thousands of notes and this is parsed on a 512 MB Pi Zero 2 W"), plus optional
`meta` and `tempo`.

Per-note fields (`lightscore-v1.schema.json:55-73`):

| Key | Type | Meaning |
|---|---|---|
| `t` | int, ms ≥0 | onset |
| `d` | int, ms >0 | duration |
| `p` | int, 21–108 | MIDI pitch (88-key range A0–C8) |
| `h` | `"L"` \| `"R"` | hand |
| `f` | int, 1–5 | finger (1=thumb..5=little) — the LED colour, the whole point of the format |
| `stf` | int ≥1 | originating staff, display only, hand lives in `h` |
| `v` | int, 0–127, optional | velocity, if the source had one |

Optional `meta.fingering` records provenance: `"dp"` / `"score"` (marks already in
source) / `"manual"` / `"mixed"`; `meta.fingering_cost` carries the DP's total path
cost for cross-release regression, unused at playback. Optional top-level `tempo` map
(`{t, bpm}` pairs) is for display/beat re-derivation only — **playback never needs
it**, every note already carries absolute ms.

**Verified**: schema validates the fixtures; a negative control (`f=9`, out of the
1–5 range) is correctly **rejected** — the validator can fail (VERIFIED).

## A1 — the fingering DP

`score/src/fingerlight_score/fingering.py:1`. Ported from the *structure* of
`scoreforge/src/scoreforge/tab.py:245` (`assign_frets`, VERIFIED file:line) — group
notes into simultaneous chord events, enumerate bounded candidate voicings per event,
forward DP to the globally cheapest path, backtrack. **Not a code import**: the whole
`score/` package is stdlib-only and never imports `scoreforge`, sidestepping its
`guitarpro`/`basic_pitch` dependencies and `<3.12` Python pin entirely (ambient
interpreter is 3.12.7).

**The inversion that matters.** A guitar tab has no finger numbers, so `assign_frets`
tracks `(string, fret)` plus a scalar hand-position proxy for transitions. Piano
fingering has real finger identity as its whole output — it becomes the LED colour —
so here **finger identity is the DP's state**, and the transition cost is directly
about which finger moves to which pitch. This "predict-then-penalize-deviation" shape
was flagged as not portable to guitar by the fingering survey but is exactly right for
piano.

Candidate generation is hard-pruned to *monotonically increasing* finger number with
increasing pitch within a chord (thumb-most digits on the lowest notes) — not just a
speed hack (turns 5! = 120 permutations into ≤ C(5,k) = 10), it reflects how chords are
actually played. The DP across time stays open to non-monotonic sequences.

### Cost model (`COST_WEIGHTS`, `fingering.py:123`)

| Term | What it penalizes |
|---|---|
| `stretch_per_semitone` + `comfort_span` | excess beyond a finger pair's comfortable stretch, intra-chord or inter-event |
| `order_violation` | intra-chord finger crossing (currently always 0.0 under monotonic-only candidates; kept evaluated, not assumed, for future non-monotonic generators) |
| `transition_cross_base` + `transition_cross_per_semitone` | thumb-under / finger-crossing between events, scaled by the semitone gap crossed |
| `finger_jump_per_number` | any finger change, proportional to how many finger-numbers apart |
| `black_key_thumb` | thumb landing on a black key |
| `repeat_finger_moved` | reusing a finger across a pitch change (free if pitch is unchanged) |
| `finger_spacing_mismatch` (**3.0**, new) | intra-chord only: deviation from an evenly-spread finger assignment |

`finger_spacing_mismatch` closes a tie-break defect: on a root C major triad (C4/E4/G4)
both **1-2-4** and **1-3-5** cost exactly 0.0 under every other term, so enumeration
order used to pick 1-2-4. The new term compares each adjacent finger pair's pitch gap
against the chord's evenly-spread expectation, so **1-3-5** — the natural open-triad
shape — now wins. Because both pinned scales are monophonic (one note per event), this
term is identically 0.0 for them and moves neither result.

**Verified** (brute force over all 5⁸ = 390,625 fingerings of an 8-note scale,
`score/tests/test_fingering.py`):

| Check | Result |
|---|---|
| Ascending C major scale | **1-2-3-1-2-3-4-5**, unique optimum, cost **20.0** |
| Descending | **5-4-3-2-1-3-2-1**, unique optimum, cost **20.0** |
| Emitted light-score | validates against schema, round-trips through JSON |
| End to end | notes → DP → light-score → firmware reader → practice session, 8/8 events, 0 errors |

Both pinned scales remain the unique optimum at unchanged cost 20.0 after
`finger_spacing_mismatch` was added.

### The left-hand mirroring bug, found and fixed

The first version fingered the left hand **as though it were a right hand** — thumb on
the lowest note. Wrong on **13 of 21** notes of the two-hand fixture. Every structural
check passed the whole time it was live: all notes present, all fingers 1–5, no finger
reused inside a chord, **15/15 tests green**, schema valid. Only the orientation was
wrong, and nothing asserted orientation.

Fix: `_axis(pitch, hand)` (`fingering.py:194`) maps pitch onto a hand-relative axis
(identity for R, negated for L); every *direction* comparison — chord ordering,
thumb-under detection — is stated on that axis instead of raw pitch (distances stay
absolute, unaffected). The right hand is unchanged by construction. Disagreement with
the fixture fell **13/21 → 7/21** (all seven legitimate alternative fingerings);
hand-groups violating orientation fell **4 → 0**. Pinned by
`score/tests/test_hand_orientation.py`, itself confirmed to fail when the fix is
reverted.

## A2 — hand assignment (Q-09) CLOSED

`score/src/fingerlight_score/hands.py:1`, a pass *before* the fingering DP.
`fingering.py` delegates to it via `resolve_hand_mode` (`hands.py:65`) and
`assign_hands` (`hands.py:80`).

| Mode | Behaviour |
|---|---|
| `"staff"` | original mapping, `staff==2→L` else `R`; unchanged, byte-identical output to pre-A2 |
| `"pitch"` | split by 1-D 2-means over *distinct* pitches (`_split_point`, `hands.py:105`), then a 2-state (L/R) Viterbi smoothing pass (`_smooth_hand_sequence`, `hands.py:159`) so a hand doesn't flip on a single transiently-crossing note |
| `"auto"` (**default**) | `"staff"` when the source carried a real `<staff>` element, `"pitch"` otherwise |

**Widest-gap heuristic tried and rejected**: on the grand-staff fixture, the left
hand's own internal chord gap (C3→G3, 7 semitones) exceeds the true inter-hand
boundary gap (G3→C4, 5 semitones), so picking the single widest pitch gap finds the
wrong split. 2-means instead minimizes within-side spread, a global property of the
whole distribution, and finds the correct boundary.

The Viterbi smoothing charges `_SWITCH_COST` (9.0) to flip hands between two
different-onset notes, but **nothing** between same-onset notes — a genuine two-hand
chord straddling the split pays no penalty for "flipping."

**Verified** (staff-stripped grand staff fixture): `"auto"` recovers **4/5** bass notes
to L and agrees with the real staff on **10/11** notes; the one disagreement is the
anti-flip guarantee working as designed — a documented limit of pitch-only inference,
not a bug.

`Note.staff_is_explicit` (`notes.py:39`, default `True`) records whether `staff` came
from a real source marking or was defaulted; only `musicxml.py`'s `_walk_part` (the
sole producer that can tell the difference) sets it `False`.

## A3 — MusicXML ingest

`score/src/fingerlight_score/musicxml.py:1`. Public surface: `parse_musicxml(path,
*, part=None)` (`musicxml.py:441`) → `MusicXmlScore(notes, tempo_map, meta)`;
`musicxml_to_lightscore(path, *, part=None, hand_mode="auto", meta=None)`
(`musicxml.py:501`) chains ingest → fingering → emit. Parses via
`defusedxml.ElementTree` — scores arrive from the internet and stdlib `xml.etree` is
vulnerable to entity-expansion attacks — falling back to stdlib only if `defusedxml`
cannot be imported.

**Verified against MuseScore 4.7.4's own MIDI render**, not typed expectations. The
SMF reader used as ground truth (`score/tests/midi_ground_truth.py`) was itself
checked: tempo bytes decode to exactly **120 bpm** (`midi_ground_truth.py:109`,
`us_per_quarter = 500000`), and the articulation gate ratio stays constant at
**0.948–0.9495** across notes of 500/1000/2000 ms. Result: **11 notes**, `(t_ms,
pitch)` sets **exactly equal** between parser and MIDI ground truth.

Handles, each verified this way rather than by inspection:

| Case | Trap if wrong |
|---|---|
| `<backup>` / `<forward>` | left hand lands after the right instead of under it |
| `<chord/>` | cursor must not advance |
| `<divisions>`, incl. mid-score change | every duration off by a constant factor |
| ties across barlines | one 2000 ms note becomes two of 1000 ms |
| mid-score tempo change | read tempo once and everything after is wrong |
| `<grace/>` | skipped and warned; does **not** steal cursor time from later notes |
| two voices on one staff | key hand off *voice* or note goes to wrong hand |
| repeats | **NOT unfolded**, deliberately — warned instead. MuseScore's own MIDI *does* unfold: **12 notes vs. our 8** on the repeat fixture |
| `.mxl` | inner file `score.xml`, reachable only via `META-INF/container.xml` |
| pitches outside 21–108 | dropped and warned (cannot be shown on an 88-key bar) |

**The quiet one worth knowing**: a score with **no `<staff>` elements at all** (a lead
sheet, a bare export) lands entirely on staff 1 (`musicxml.py:365`). Output is
structurally perfect — right note count, legal fingers, valid light-score — **only the
hands are wrong**. `_walk_part` now warns and marks every such note
`staff_is_explicit=False`, so `hand_mode="auto"` falls back to `"pitch"` instead of
silently mis-fingering the whole piece as one hand. This was the concrete case Q-09
was opened to solve, and A2 closes it.

End to end: MusicXML → DP → light-score → firmware reader → practice session, schema
valid, 0 errors.

## A4 — OMR arm (Q-08)

`score/src/fingerlight_score/omr.py:1`. Third input, alongside MIDI and MusicXML: a
PDF or photo becomes MusicXML that `musicxml.py` already reads. Public surface:
`omr_to_musicxml(path, ...)` (`omr.py:579`), `omr_to_lightscore(path, ...)`
(`omr.py:615`), chaining into `musicxml_to_lightscore` and carrying OMR warnings into
the light-score's `meta`.

Runs **on the PC only, never the Pi**: [Audiveris](https://github.com/Audiveris/audiveris)
is a JVM app wanting 2 GB+ heap; the Zero 2 W has 512 MB. Windows install:
`C:\Program Files\Audiveris\Audiveris.exe` (bundles its own JRE).
`locate_audiveris()` (`omr.py:101`) resolves an explicit path, then
`FINGERLIGHT_AUDIVERIS` env var, then `PATH`, then the default install location, else
raises `AudiverisNotFoundError`.

Command (`AUDIVERIS_CLI_ARGS`, one module constant, `omr.py:82` / `run_audiveris`
`omr.py:142`), verified against Audiveris' own handbook (fetched 2026-09-16), **not**
run against the real binary:

```
Audiveris.exe -batch -transcribe -export -output <output_dir> -- <input_file>
```

### The cleanup — proven load-bearing

`merge_two_staff_parts` (`omr.py:299`) and `sanity_report` (`omr.py:430`), chained by
`clean_omr_musicxml` (`omr.py:554`). Raw OMR exports a grand staff as two separate
`<part>`s with no `<staff>` markup anywhere; `merge_two_staff_parts` splices them into
one part, assigns staff 1/2 by which part has the higher average pitch, and injects
`<staff>` into every note. `sanity_report` flags out-of-88-key pitches, bad/missing
durations, and measures that don't sum to their time signature — a structured list of
`OmrWarning(kind, message)`, never silently dropped.

**Measured effect on the hand-authored fixture** (imitates Audiveris' defect, never
produced by it):

| | Notes | On staff 2 |
|---|---|---|
| Without cleanup | 6 | 0 (the whole bass part silently missing) |
| With cleanup | 9 | 3 |

### What is unverified

**The subprocess call itself has never executed on this machine** — no JVM, no
Audiveris install. Everything above the cleanup line (`locate_audiveris`'s
PATH/env/default-path lookup, `run_audiveris`'s command construction) is exercised
only by mocking `subprocess.run`, plus the real (unmocked) "not found" error path,
which genuinely fires here. The output-filename assumption
(`<input-stem>.mxl`) is a documented guess with a fallback. If the real binary
rejects `AUDIVERIS_CLI_ARGS` or names its output differently, fix `omr.py` and
re-verify against a real install before trusting this arm end to end (UNVERIFIED).

`omr_to_musicxml` accepts either an image/PDF (invokes Audiveris) or an
already-MusicXML/`.mxl` file (skips straight to cleanup) — this is how the fixture is
tested without a real OMR pass.

## Modules at a glance

| Module | Role |
|---|---|
| `notes.py:18` (`Note`) / `notes.py:43` (`FingeredNote`) | minimal note model, deliberately not `scoreforge.tab.Note`'s shape; `t_ms`/`d_ms` match lightscore's native units so `emit.py` needs no conversion |
| `fingering.py` | chord grouping, candidate generation, cost model, per-hand DP, `assign_fingering(notes, hand_mode="auto")` (`fingering.py:391`) |
| `hands.py` | hand assignment pass, `"staff"`/`"pitch"`/`"auto"` |
| `musicxml.py` | MusicXML/`.mxl` ingest |
| `omr.py` | Audiveris subprocess wrapper + post-OMR cleanup |
| `emit.py` | `to_lightscore` (`emit.py:19`) / `write_lightscore` (`emit.py:51`) — direct field mapping, notes sorted by `(t, p)`, atomic write via temp file + `os.replace` (never truncate-in-place) |

## Status and open items

**68 score tests pass on the PC** (VERIFIED, `python -m pytest score/tests -q`,
2026-09-16). A0–A4 all closed per the table above; the only remaining flag is A4's
Audiveris subprocess call (UNVERIFIED, needs a real JVM + install).

Known open item: the cost model (`COST_WEIGHTS`) has only ever been exercised against
scales and chord fixtures — **never a real piece**. Tuning against real repertoire is
unstarted.

See [index](index.md), [log](../log.md), [decisions](decisions.md), [hardware](hardware.md), [firmware](firmware.md), [provenance](provenance.md).
