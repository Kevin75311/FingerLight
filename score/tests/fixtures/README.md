# MusicXML test fixtures — provenance

These exist so the MusicXML ingest is checked against something that **did not come from
our parser**. A parser agreeing with expectations typed by its own author proves nothing.

## The chain

```
grand-staff-src.musicxml   hand-authored input; defines the musical content
        |
        |  MuseScore 4.7.4  (C:\Program Files\MuseScore 4\bin\MuseScore4.exe)
        v
grand-staff.musicxml       canonical re-export -- THE TEST INPUT
grand-staff.mxl            same score, compressed container
grand-staff.mid            MuseScore's own render -- THE GROUND TRUTH
```

Regenerate with:

```
MS="C:/Program Files/MuseScore 4/bin/MuseScore4.exe"
"$MS" grand-staff-src.musicxml -o grand-staff.musicxml
"$MS" grand-staff-src.musicxml -o grand-staff.mid
"$MS" grand-staff-src.musicxml -o grand-staff.mxl
```

Read the MIDI back with `python score/tests/midi_ground_truth.py grand-staff.mid`
(a ~70-line SMF reader written for this purpose, independent of the MusicXML path).

## What the score deliberately exercises

Each of these fails *silently* in a naive reader — plausible output, no error:

| Feature | The trap |
|---|---|
| `<backup>` (6 in the export) | the grand staff's second voice rewinds the cursor; ignore it and the left hand lands **after** the right hand instead of under it |
| `<chord/>` (2) | shares the previous note's onset; the cursor must not advance |
| `divisions` | durations are in divisions, not quarters |
| `<rest>` (2) | advances the cursor, emits no note |
| tie across a barline (4 tie tags) | G4 in m2–m3 is **one** note of ~2000 ms, not two of 1000 ms |
| `<alter>` (2) | F#4 → MIDI 66, B♭4 → MIDI 70 |
| `<staff>` (21) | 1 = right hand, 2 = left hand; this feeds `hand_mode="staff"` |
| `.mxl` container | the inner file is **`score.xml`**, not a fixed name — `META-INF/container.xml` must be read to find it |

## Ground truth — MuseScore's MIDI render

11 notes. Onsets and pitches are exact; **durations are not**.

| t (ms) | pitch | note | staff | MIDI d (ms) | notated d (ms) |
|---:|---:|---|---:|---:|---:|
| 0 | 60 | C4 | 1 | 474 | 500 |
| 0 | 48 | C3 | 2 | 949 | 1000 |
| 0 | 55 | G3 | 2 | 949 | 1000 |
| 500 | 62 | D4 | 1 | 474 | 500 |
| 1000 | 64 | E4 | 1 | 949 | 1000 |
| 2000 | 66 | F#4 | 1 | 474 | 500 |
| 2000 | 41 | F2 | 2 | 1899 | 2000 |
| 2000 | 48 | C3 | 2 | 1899 | 2000 |
| 3000 | 67 | G4 *(tied)* | 1 | 1899 | **2000** |
| 4000 | 43 | G2 | 2 | 1899 | 2000 |
| 5000 | 70 | B♭4 | 1 | 949 | 1000 |

> **Durations carry an articulation gap.** MuseScore shortens every note to ~94.9% of its
> notated length so repeated notes re-attack cleanly. So compare **onsets and pitches
> exactly**, and durations only against the *notated* value with tolerance — never assert
> our parser reproduces the MIDI durations, which would bake MuseScore's playback
> convention into our score model.
>
> The single most valuable row is **G4 at t=3000**: one note of ~1900 ms. A parser that
> mishandles ties emits two notes, at 3000 and 4000. Nothing else in the table changes.

---

# The second fixture — `hard-src.musicxml` → `hard.musicxml` / `hard.mid`

Exists because the first fixture reaches none of these, and the ingest originally
claimed them "implemented, verified by code reading only". Same MuseScore chain.

| Feature | The trap |
|---|---|
| `<grace/>` | no `<duration>`; if it consumes cursor time, **every later note in the measure slides** |
| mid-score tempo change | measure 2 drops to 60 bpm, so its quarter is 1000 ms where measure 1's is 500 ms; read tempo once and everything after is wrong |
| repeat barlines | measure 2 sits inside `|: :|` |
| two voices on one staff | voice 1 **and** voice 2 both on staff 1; a parser keying hand off *voice* instead of *staff* sends A3 to the left hand |

## The deliberate divergence

**MuseScore unfolds the repeat; we do not.** Its MIDI has 12 notes (measure 2 twice,
second pass starting at 6000 ms); our parser emits 8 and warns. That is by design — the
light-score is a practice cue, and the player decides how many times to play a repeat.

So the cross-check compares only `t < 6000` and drops the zero-length grace note. A
negative control asserts that the *unfiltered* comparison fails, so the filter is doing
real work rather than matching trivially.

Ground truth, first pass only:

| t (ms) | pitch | note | staff | why it matters |
|---:|---:|---|---:|---|
| 0 | 60 | C4 | 1 | must stay at 0 — the grace note must not push it |
| 0 | 57 | A3 | **1** | voice 2 but **staff 1** |
| 0 | 48 | C3 | 2 | |
| 500 | 64 | E4 | 1 | |
| 1000 | 67 | G4 | 1 | |
| 2000 | 60 | C4 | 1 | measure 2 at 60 bpm — this half note is **2000 ms**, not 1000 |
| 2000 | 43 | G2 | 2 | whole note, **4000 ms** |
| 4000 | 62 | D4 | 1 | |

The grace D4 appears in MuseScore's MIDI at t=0 with **d=0**; we skip it and warn.

## A third case, with no fixture file

A score carrying **no `<staff>` elements at all** (a lead sheet, or a bare export) is
built in-memory in `test_musicxml_hard.py`. Everything lands on staff 1 and is fingered
as one hand — structurally perfect output, wrong hands. The ingest warns; solving it
properly is the project's **Q-09**.

---

# The OMR fixture — `raw-audiveris-grand-staff.musicxml`

**This file is hand-authored to imitate raw Audiveris output. It was never produced by
Audiveris.** There is no Java/Audiveris install on the machine this was built on (see
`docs/score/omr.md`), so nobody should mistake this for ground truth the way the two
fixtures above are — it exists only to exercise `fingerlight_score/omr.py`'s post-OMR
cleanup against the specific defect pattern that module targets:

| Feature | The trap |
|---|---|
| grand staff exported as **two separate `<part>`s** (`P1`, `P2`) | naive ingest picks only one part (`musicxml.py`'s `_select_part`) and the other hand vanishes entirely |
| **no `<staff>` element anywhere** in either part | even if both parts were merged naively, every note would default to staff 1 (`musicxml.py`'s "no `<staff>` elements" warning) -- fingered as one hand |
| one out-of-range pitch: `C0` (MIDI 12, part `P2` measure 1) | below the 88-key range 21..108; `omr.py`'s `sanity_report` must flag it, and `parse_musicxml` must drop it (not crash) |

10 notes authored: `P1` (treble, becomes staff 1) has C4 D4 E4 / F4 G4 A4 (6 notes, all
in range). `P2` (bass, becomes staff 2) has C0 / C2 G2 C3 (4 notes, one -- C0 -- out of
range). `fingerlight_score.omr.merge_two_staff_parts` picks staff 1 vs. staff 2 by
**average MIDI pitch** (P1's 64.5 > P2's 34.8), not by `<part-list>` order.

Expected result **with** cleanup (`omr_to_musicxml` / `clean_omr_musicxml`): 9 notes
total (C0 dropped by `parse_musicxml` itself as out-of-range), 3 of them on staff 2 --
the bass notes (C2/G2/C3, MIDI 36/43/48).

Expected result **without** cleanup (`parse_musicxml` on the raw fixture directly): only
6 notes (`P1`'s treble line -- `_select_part` picks the first part with notes and warns
about the ignored one), **0** notes on staff 2 -- the entire bass part silently missing.
`score/tests/test_omr.py::test_without_cleanup_the_fixture_parses_wrong` pins this as a
negative control, so the cleanup is proven load-bearing rather than cosmetic.
