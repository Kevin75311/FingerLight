This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## What this project is

**ScoreForge** (formerly Guitar Tab Generator): a local pipeline + web GUI that turns
a song audio file into a guitar TAB (`.gp5`) or piano sheet music (cleaned MIDI →
MuseScore PDF). Handles full mixes (Demucs separation first) and solo-instrument
recordings (`--source solo`, separation skipped). Optional extras: chord detection
(guitar), vocal-melody extraction (mix sources). See
`AI Guitar Transcription Workflow.md` for the original design rationale and
`docs/research/*.md` for the July 2026 surveys behind the tool choices
(model-survey, fingering-survey, deployment-gui-survey).

## Build / run / test commands

```bash
# One-time setup -- requires Python 3.11 (Demucs pins torchaudio<2.1, Basic Pitch pins
# tensorflow<2.15.1; neither has cp312+ wheels)
conda create -n scoreforge python=3.11 -y && conda activate scoreforge
pip install -e .                          # this package + deps incl. fastapi/uvicorn
pip install -e external/demucs            # submodule, not installed by pip -e .
pip install -e external/basic-pitch       # submodule, not installed by pip -e .
pip install piano-transcription-inference # optional: piano-specialized model
# madmom-prebuilt needs a one-time dist-info fix after install -- see README
# "Chord detection setup" (pipeline soft-skips chords without it).

# CLI
python run_pipeline.py song.mp3 [--instrument piano] [--source solo] [--vocal-melody]

# Web GUI (FastAPI + bundled static frontend, http://127.0.0.1:8765)
python -m scoreforge

# Unit tests (pure logic only: fret-assignment DP, melody reduction, tempo
# correction, note merging, chord parsing -- no MIDI/audio/madmom I/O needed)
python -m unittest discover -s tests -v
```

No real-audio integration test in CI -- Demucs/Basic Pitch/madmom inference needs
multi-GB models; exercised manually. (The webui + solo path has been exercised
end-to-end with a synthetic wav through real Basic Pitch.)

## Architecture

Pipeline stages, orchestrated by `src/scoreforge/pipeline.py` (all intermediates
under `output/<song_stem>/`); `run_pipeline()` takes a `progress` callback the web
GUI uses for SSE updates:

1. **`separate.py`** -- shells out to the `demucs` CLI. Skipped entirely for
   `source="solo"`. Reuses existing stems from a prior run unless
   `reuse_existing=False`. `other` stem = guitar approximation; piano auto-switches
   to 6-stem `htdemucs_6s` (`STEM_NAMES_BY_MODEL`).
2. **`transcribe.py`** -- `transcribe()` shells out to the `basic-pitch` CLI;
   `transcribe_piano()` uses bytedance `piano_transcription_inference` (soft
   dependency, MIT, much better on piano) with fallback to Basic Pitch. Both
   subprocesses get `PYTHONIOENCODING=utf-8` forced -- basic-pitch prints emoji and
   dies on cp1252 when stdout is a pipe (i.e. under the web GUI). CLI tools are
   resolved via `util.find_executable()` which checks next to `sys.executable`
   too, because the GUI may be launched without the conda env's Scripts on PATH.
3. **`chord.py`** -- optional madmom chord recognition on the guitar stem,
   independent of the MIDI transcription (that independence enables the
   chord/note cross-check). Guitar-only: piano mode skips it (result was unused).
   `detect_chords()` is the only madmom-importing function; the rest is pure.
4. **`melody.py`** -- pure `reduce_to_melody()`: Basic Pitch output on the vocals
   stem → monophonic lead line (salience = duration > velocity > pitch; full
   overlaps compete, boundary smears get trimmed). Guitar: extra `.melody.gp5` +
   `.tab.txt`; piano: extra track inside the piano MIDI.
5. **`tab.py`** -- the core original logic:
   - `assign_frets()` is a **dynamic program over the whole song**: per chord
     event, `_enumerate_event_candidates()` backtracks to the top-K voicings
     (count beats playability), then a forward DP charges `W_MOVEMENT` (100) per
     fret of hand movement between events; intra costs are span×10 + position×1
     − open-string bonus, with a steep `WIDE_SPAN_PENALTY` past
     `MAX_COMFORT_SPAN`. Weights adopted from noahbaculi/guitar-tab-generator
     (docs/research/fingering-survey.md). All-open voicings are
     position-transparent (no movement charge, position passes through).
   - `merge_repeated_notes()` / `correct_tempo_octave()` -- confirmed fixes for
     Basic Pitch oversegmentation and pretty_midi tempo octave errors.
   - `build_gp5_from_midi()` (loads + estimates tempo) delegates to
     `build_gp5_from_notes()` (in-memory notes, explicit tempo -- used by the
     melody path). `build_piano_midi(extra_tracks=...)` writes multi-track MIDI.
   - `render_ascii_tab()` -- MuseScore-independent text tab (MuseScore's .gp5
     importer drops the tablature flag; manual Instruments→Tablature step needed
     for a tab staff in the PDF -- see README).
   - Constant note velocity on purpose (velocity IS the dynamic marking in gp5).
6. **`render.py`** -- optional MuseScore PDF render; soft-skips if not on PATH.

**Web GUI**: `webui.py` (FastAPI `create_app()`) + `static/` (no-build-step SPA:
index.html/style.css/app.js) + `__main__.py` (`python -m scoreforge`, uvicorn).
Single worker thread runs one job at a time; per-stage progress + captured stdout
stream to the browser over SSE (`/api/jobs/{id}/events`). The same static dir is
deployable to Cloudflare Pages (`wrangler.toml`; frontend asks for a local engine
URL when not same-origin) -- the engine itself cannot run on Cloudflare (no
Python/GPU; see docs/research/deployment-gui-survey.md).

## Git submodules (`external/`)

- `external/demucs`, `external/basic-pitch` -- invoked via CLI, installed manually.
- `external/midi-to-guitar-tab` -- **reference only**, PyGuitarPro API worked
  example; its chord-symbol fret logic was deliberately not reused.
