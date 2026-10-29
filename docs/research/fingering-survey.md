---
name: fingering-survey
aliases: [Fingering survey]
tags: [research, score, fingering]
---

# Fret/fingering assignment survey — for upgrading `assign_frets()`

Researched 2026-07-11. Goal: replace the current greedy-per-event backtracking
search in `src/guitar_tab_generator/tab.py` (`_best_assignment_for_event` /
`_score_assignment`, tab.py:139-212) with a proper DP/shortest-path over
per-chord-event voicing candidates. Current cost function (tab.py:139-153):
`span * 1.0 + movement * 0.5` where `span = max(fret) - min(fret)` and
`movement = |avg_fret(event) - avg_fret(prev_event)|`. Only the single best
assignment per event is kept (no k-best carry-forward), so a locally-good
choice can trap a later event into a bad one — this is the main structural
gap vs. all three surveyed tools below.

---

## 1. jgollub1/guitar_dp

**URL:** https://github.com/jgollub1/guitar_dp · **License:** MIT (root
`LICENSE`; a stray `license.txt` also exists, marked "Unknown" by GitHub but
not authoritative — MIT applies).

**What it is:** a Flask demo (`app/`) over a single Python module,
`guitar_util.py` (~75 lines, Python 2 — uses `sys.maxint` and
`dict.iteritems()`, will not run under Python 3 without porting). Solves
**monophonic** melody fingering only — one note at a time, no chords/polyphony.

**State definition:** each note in the input sequence is expanded into all
`(string, fret, finger)` triples that can produce that pitch (`generate_path`,
`guitar_util.py`), where `finger ∈ {1,2,3,4}` is tried independently of fret
(i.e. finger is a free variable, not derived from a hand-position model).

**DP formulation** (`compute_path`, `guitar_util.py`): classic per-note
forward DP. `final_paths[i]` maps each candidate triple at note `i` to
`(min_cost_so_far, best_path_so_far)`. For each triple at note `i+1`, it scans
*every* triple retained at note `i` (no pruning/beam) and keeps the
minimum-cost predecessor. `O(sum_i |cands_i| * |cands_{i-1}|)`.

**Transition cost** (`compute_cost(tup1, tup2)`, verbatim):
```python
def compute_cost(tup1, tup2):
    string1, fret1, finger1 = tup1
    string2, fret2, finger2 = tup2
    expected_fret = fret1 + (finger2 - finger1)
    return (fret2 - expected_fret)**2
```
i.e. it predicts where finger2 "should" land on the fretboard given finger1's
fret and the change in finger number (assumes fingers are laid out roughly
one fret apart along the hand), then squared-penalizes the actual fret2's
deviation from that prediction. No string-change cost, no absolute
hand-position cost. **`compute_cost2` and `compute_cost3` are declared but
unimplemented (empty `return`)** — the author's own comments say these were
meant to add string-change and absolute-position costs and never got written.

**Input/output:** input is a MIDI note sequence (`notes`, a list of pitch
ints) mapped onto a fixed layout (`STARTS`/`RANGES` constants hardcode
standard tuning, frets 0-11/13/17 per string). Output is a list of
`"string.fret"` strings for the optimal path (`reformat`).

**Code quality:** small, readable, but unmaintained (Python 2), incomplete
(2 of 3 cost functions are stubs), and monophonic-only — cannot be used as-is
for chord events. **What's worth porting:** the finger-aware transition-cost
*idea* (predict next finger's fret from current finger+fret, penalize
deviation) is a legitimate biomechanical proxy that our current cost model
lacks entirely (we track chord centroid, not per-finger commitments). Not
worth porting the code itself — reimplement the idea, extended to chords, if
we ever want finger-level modeling; not necessary for a first DP upgrade.

---

## 2. hyvyys/chord-fingering

**URL:** https://github.com/hyvyys/chord-fingering · **License:** GPL-3.0.

**What it is:** a small CommonJS/JS chord-*voicing* generator (not a
sequence/tab arranger) — given a chord symbol (e.g. `"Am7"`) and a tuning, it
enumerates and ranks playable fingerings for that single chord, independent
of any melodic/song context. Built for the "Chordline" app; ships 111 chord
shape patterns and unit tests per module (`src/*.test.js`).

**Relevant files:** `src/positions.js` (`findPositions` — for each requested
chord tone, finds every `(string, fret)` within the first octave of the
fretboard that produces it; no span/capo pruning at this stage, "generate
everything, filter later"), `src/fingerings.js` (filtering + ranking),
`src/barre.js` (barre-chord detection).

**Filtering (`fingerings.js`):**
- `MAX_FRET_DISTANCE = 3` — reachable frets must be within 3 of each other
  for non-barre fingers (`Math.abs(position.fret - fret) <= MAX_FRET_DISTANCE`).
- Finger-count cap: ≤4 fretting fingers normally, ≤3 non-barre fingers when a
  barre is used (`HUMAN_LEFT_HAND_PLAYING_FINGERS`).
- Rejects "anatomically invalid" shapes (fret range ≥3 without a barre where
  both the min and max fret already have 4 fingers committed).
- All required chord-tones must be present; optional tones may be dropped.

**Ranking (priority-ordered comparator, not a single scalar cost):**
1. prefer 4-string voicings, 2. minimize muted strings between the lowest and
highest played string, 3. maximize unique-notes/positions ratio, 4. apply
`limitDifficulty(f, 14)` / `limitDifficulty(f, 12)` caps (two difficulty
tiers), 5. prefer lower bass fret, 6. prefer lower max fret, 7. lower overall
computed difficulty.

**Reusability for our pipeline:** conceptually useful but not directly
applicable — this tool answers "what are the good shapes for *this named
chord*" (music-theory chord symbol → voicing), whereas our `assign_frets()`
works from raw transcribed pitches per chord *event* with no chord-symbol
step (per CLAUDE.md, deliberately not using `music21`/chord-name lookups, the
same design choice this repo's own reference `midi-to-guitar-tab` made and we
rejected). What's portable as an idea: (a) the **priority-ordered
comparator** pattern instead of one weighted scalar — easier to tune/debug
than summing weighted terms; (b) `MAX_FRET_DISTANCE`-style reachability
pruning of candidates *before* scoring, which we don't currently do (we prune
only by absolute `max_fret`, not by span-from-each-other); (c) explicit muted
open-string handling between played strings, which we don't model at all.

---

## 3. noahbaculi/guitar-tab-generator

**URL:** https://github.com/noahbaculi/guitar-tab-generator · **License:**
GPL-3.0. **Language:** Rust (compiled to WASM for the web frontend); has
`benches/`, `tests/`, `docs/` (ADRs) — the most professionally engineered of
the three.

**Input:** plain text, **not audio** — newline-separated pitch names (e.g.
`E2`, `A2`, `D3`); a blank line = a rest; simultaneous newline-adjacent
pitches (no blank line between) form a chord/beat. Output: ranked ASCII tab
renderings, one per candidate arrangement.

**Algorithm — this is a true graph shortest-path DP, not greedy:**
- **Candidate generation** (`src/guitar.rs`, `generate_pitch_fingerings`):
  for each pitch, and each string, computes the fret via a precomputed
  per-string pitch range (`create_string_range`); capo is handled by shifting
  each string's open pitch up by the capo's semitones and shrinking the
  playable-fret range accordingly. For chords, per-pitch fingering options are
  combined via a **Cartesian product**, filtered to only combinations where
  every pitch lands on a **distinct string** — this is the direct analogue of
  our `_best_assignment_for_event`'s used-string constraint. No explicit
  max-span pruning at this stage (unlike chord-fingering's `MAX_FRET_DISTANCE`);
  span is handled entirely via the cost function below.
- **Graph** (`src/arrangement.rs`): a layered DAG, one layer per beat/line.
  ```rust
  enum Node {
      Start,
      Rest { line_index: u16 },
      Playable { line_index: u16, scored_beat_fingering: Rc<ScoredBeatFingering> },
  }
  ```
  A `Playable` node = one complete candidate fingering for one beat (one
  `PitchFingering` per pitch in that beat, all on distinct strings). Each node
  precomputes two scalar features once: `avg_non_zero_fret` (mean fret across
  fretted, i.e. non-open, notes in that beat) and `non_zero_fret_span`
  (max−min fret among fretted notes). Edges connect every node in layer *i* to
  every node in layer *i+1* (full bipartite between adjacent layers, not
  restricted) — i.e. no pruning of the candidate set before shortest-path.
- **Search:** **Yen's k-shortest-paths algorithm** (built on Dijkstra) over
  this DAG, returning the top-N cheapest complete paths from `Start` through
  every beat — so the tool natively supports "show me the 3 easiest
  arrangements," not just one.
- **Cost function** (`calculate_node_difficulty`, `src/arrangement.rs`,
  transcribed from the fetched source):
  ```rust
  fn calculate_node_difficulty(current_node: &Node, next_node: &Node, weights: DifficultyWeights) -> NodeDifficulty {
      let current_avg_fret = match current_node {
          Node::Playable { scored_beat_fingering, .. } => scored_beat_fingering.avg_non_zero_fret,
          _ => None,
      };
      let (next_avg_fret, next_fret_span) = match next_node {
          Node::Rest { .. } => (None, 0.0),
          Node::Playable { scored_beat_fingering, .. } =>
              (scored_beat_fingering.avg_non_zero_fret, scored_beat_fingering.non_zero_fret_span as f64),
          _ => unreachable!(),
      };
      let avg_fret_difference = match (current_avg_fret, next_avg_fret) {
          (Some(curr), Some(next)) => (next - curr).abs(),
          _ => 0.0,
      };
      OrderedFloat(
          (avg_fret_difference * weights.movement())
          + (next_fret_span * weights.span())
          + (next_avg_fret.unwrap_or(OrderedFloat(0.0))).into_inner() * weights.position()
      )
  }
  ```
  Standard weights: **movement = 100, span = 10, position = 1**. So this is
  edge cost = weighted sum of (a) hand-movement between consecutive beats'
  average fretted fret, (b) the *destination* beat's own fret span, (c) the
  destination beat's absolute average fret (a "playing high up the neck is
  inherently harder" term our cost model has no equivalent of). Total path
  cost = sum of edge costs, exactly analogous to what a full DP over our
  chord-event sequence would accumulate. **Does not model** string skips,
  finger reuse/identity, muting, or open-string preference explicitly —
  those are left implicit in which candidate fingerings even exist.
- Not directly usable as a library from Python (it's Rust/WASM with a JS/web
  packaging story, `docs/` ADRs and `MIGRATION.md` suggest an evolving public
  API) — nothing to import, but the algorithm is small enough to port whole.

### Comparison metrics (for benchmarking our output against this tool's)

Three metrics computable from either tool's output with only `(string,
fret)` per note plus onset time — no audio/chord-symbol context needed:

1. **Total fret span** — sum over chord events of `max(fret) − min(fret)`
   among fretted (fret > 0) notes in that event. (Directly comparable: this
   is exactly our `_score_assignment`'s `span` term, summed over the song.)
2. **Total hand-movement distance** — sum over consecutive event pairs of
   `|avg_fret(event_i) − avg_fret(event_i-1)|` (fretted notes only, matching
   both our `movement` term and their `avg_fret_difference` term).
3. **% open-string notes** — `count(fret == 0) / count(all notes)` across the
   song; a higher fraction is easier to play (no left-hand fretting needed) and
   is a proxy neither our cost function nor theirs currently rewards directly
   (our `fretted` list literally excludes fret==0 from the span/movement
   calc, so open strings are "free" but not *preferred* over an equally-easy
   fretted alternative — same true of noahbaculi's model, which also excludes
   `fret == 0` from `avg_non_zero_fret`/span).

Optional 4th metric if useful: **max simultaneous fret span** (worst single
chord event's span, not summed) — catches whether either tool ever produces
one physically-impossible chord shape even if the *average* looks fine.

---

## 4. Other approaches (academic)

Web search (2026-07-11) surfaced a known lineage this problem sits in;
listing the most credible/cited, one line each:

1. **Sayegh (1989), "Optimum Path Paradigm for the Fingering of Guitar
   Chords"** — the foundational paper: models fingering as a shortest-path
   problem over a graph of candidate hand positions per chord, transition
   cost = a hand-movement/biomechanical distance function; essentially the
   ancestor of every DP/shortest-path approach surveyed above (chord-tab
   pathfinding as graph search predates all three GitHub repos by ~30 years).
2. **Hori, Kameoka & Sagayama (2013), "Input-output HMM applied to automatic
   arrangement for guitars"; Hori & Sagayama (2016), "Minimax Viterbi
   Algorithm for HMM-Based Guitar Fingering Decision"** (ISMIR 2016; found via
   Semantic Scholar) — casts fingering as an HMM where hidden states are
   left-hand fretboard *forms* (shapes) and observations are the note
   sequence; standard Viterbi minimizes total/product transition cost across
   the whole sequence, while their "minimax Viterbi" variant instead
   minimizes the *worst single transition* along the path (maximizes the
   minimum transition probability) — a meaningfully different objective from
   every DP above (all of which sum/average cost, so one bad transition can
   hide inside an otherwise-cheap path; minimax Viterbi is explicitly the fix
   for that failure mode).
3. **Burlet & Fujinaga, "A*-Guitar"** (extends Sayegh) — A* search over the
   same string/fret/hand-position graph for polyphonic tab, using an
   admissible heuristic to prune vs. exhaustive DP; relevant if our per-event
   candidate space ever gets too large for plain DP (unlikely at 6
   strings/chord-sized events, more relevant if candidate generation is
   widened, e.g. multiple capo positions tried at once).

Not independently verified beyond search-result abstracts/Semantic Scholar
listings (paywalled/no accessible PDF fetched for #1–#3); treat algorithm
summaries above as secondary-source characterizations, not confirmed by
reading the primary papers.

---

## Recommended cost model to implement (concrete, no re-reading required)

Upgrade `assign_frets()` from "one best assignment per event, one-step
lookback" (current) to a **true DP over per-event top-K candidates**,
borrowing noahbaculi's weighted-sum edge cost (cleanest to port; already
almost matches our existing terms) plus two additions found in the survey
that we currently lack (chord-fingering's reachability pruning, and an
open-string bonus motivated by metric 3 above):

- **Candidate generation per chord event:** keep our existing
  `_best_assignment_for_event`'s backtracking to *enumerate* (not just pick
  one) valid string-assignments per event — cap it at the top **K=5-8** by
  the per-event standalone cost below, to bound DP width. Add a
  chord-fingering-style **reachability filter** before scoring: drop
  candidate assignments where `max(fretted) - min(fretted) > MAX_SPAN` (e.g.
  5, configurable) — this prunes physically-impossible voicings the current
  code would otherwise let through and only penalize via score.
- **Per-event (node) standalone score:**
  `span(event) * W_SPAN + avg_fret(event) * W_POSITION`
  where `span`/`avg_fret` computed over fretted (fret>0) notes only, exactly
  as now; `avg_fret = prev_position` fallback for all-open events unchanged.
- **Transition (edge) cost between chosen candidate at event i-1 and
  candidate at event i:**
  `movement = |avg_fret(event_i) - avg_fret(event_i-1)| * W_MOVEMENT`
- **Total DP cost accumulated per candidate-path** =
  `sum_i [ span_i * W_SPAN + avg_fret_i * W_POSITION + movement_i * W_MOVEMENT ]`
  — this is a straight port of noahbaculi's formula; starting weights
  `W_MOVEMENT=100, W_SPAN=10, W_POSITION=1` (their published defaults) are a
  reasonable starting point, then re-tune against our own test songs (their
  weights were tuned for their own note-density assumptions, not ours).
- **Open-string bonus (new, not in any surveyed tool):** subtract a small
  constant `OPEN_STRING_BONUS` (e.g. 0.5–1.0, sign-negative in the cost) per
  open-string note in an event, so that when two candidates tie on
  span/movement/position, the one using more open strings wins — addresses
  the gap noted in metric 3 (open strings are currently "free" but not
  "preferred" in either our code or noahbaculi's).
- **DP mechanics:** standard forward DP/Viterbi — `dp[i][cand]` = min total
  cost of any path ending at candidate `cand` for event `i`, computed as
  `min over cand' in top-K(event i-1) of dp[i-1][cand'] + edge_cost(cand',
  cand) + node_cost(cand)`; backpointer to reconstruct. This directly fixes
  the current code's structural gap (single retained assignment per event) at
  `O(events * K^2)`, trivial cost for K≈5-8 and typical song lengths.
- **Not recommended to port:** guitar_dp's finger-identity/per-finger cost
  (adds a `finger ∈ {1,2,3,4}` dimension to every candidate — real
  biomechanical value, but multiplies the candidate space and there's no
  finger-assignment need expressed anywhere else in this codebase; revisit
  only if manual-correction feedback specifically flags bad finger
  ergonomics, not fret/string choice). Minimax-Viterbi (Hori & Sagayama) is
  worth a follow-up experiment (swap the DP's `min-sum` for `min-max` and
  compare) but is a bigger conceptual change than this first pass warrants.
