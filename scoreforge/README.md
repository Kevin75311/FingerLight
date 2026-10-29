# ScoreForge

**Forge scores from sound.** Turn any song — a full mix with vocals, a solo
guitar recording, or a solo piano recording — into a playable **guitar TAB
(`.gp5`)** or **piano sheet music (MIDI → PDF)**, fully locally and for free.
Comes with a polished local web GUI and a CLI.

*(Formerly "Guitar Tab Generator" — renamed when it stopped being only about
guitar.)*

```
song.mp3 ──► Demucs                    source separation (skipped for solo recordings)
                │
                ▼
             guitar/piano stem
                │
                ▼
             Basic Pitch (guitar)      automatic music transcription
             bytedance model (piano)   (piano-specialized, if installed)
                │
                ▼
             madmom (guitar, opt.)     chord recognition, INDEPENDENT of the notes —
                │                      chord-per-measure, capo suggestion, cross-check
                ▼
             vocal melody (opt.)       Basic Pitch on the vocals stem, reduced to
                │                      one monophonic lead line
                ▼
             DP fret assignment        this project's own code: globally-optimal
             + PyGuitarPro             voicing path over the whole song  ──► song.gp5
             (piano: cleaned-up MIDI, grand staff)                       ──► song.piano.mid
                │
                ▼
             MuseScore (optional)      render to PDF
                │
                ▼
             Manual corrections        expected — see the workflow doc's accuracy table
```

Design surveys behind the tool choices live in `docs/research/`
(`model-survey.md`, `fingering-survey.md`, `deployment-gui-survey.md`).

## The web GUI

```bash
python -m scoreforge          # opens http://127.0.0.1:8765
```

Drop a song, pick guitar or piano, say whether the recording is a full mix or
already a solo instrument, toggle the extras (vocal melody, chords, PDF), and
press **Forge the score**. Progress is drawn as notes on a six-line tab staff —
skipped stages show as `x`, tab's muted-note symbol — with a live engine log,
and every output file is downloadable when the run finishes. Everything runs on
your machine; nothing is uploaded anywhere.

## CLI usage

```bash
python run_pipeline.py song.mp3                              # vocal song -> guitar tab
python run_pipeline.py song.mp3 --vocal-melody               # + sung melody as extra tab
python run_pipeline.py song.mp3 --instrument piano           # -> piano sheet music
python run_pipeline.py riff.wav --source solo                # pure guitar recording:
                                                             #   skips separation entirely
python run_pipeline.py piano.wav --instrument piano --source solo
python run_pipeline.py song.mp3 --skip-render --debug
```

Key options (see `--help` for everything):

- `--instrument guitar|piano` — what to write a score for. Guitar builds a
  `.gp5` TAB via fret assignment; piano writes a cleaned-up `song.piano.mid`
  that MuseScore imports directly into grand-staff notation (Guitar Pro's format
  can't represent absolute pitch, only string+fret). Piano mode auto-switches
  Demucs to the 6-stem `htdemucs_6s` (the default model has no piano stem) and
  uses the bytedance piano transcription model if installed (see setup 6).
- `--source mix|solo` — **mix** (default): full band/vocal song, Demucs
  separates it first. **solo**: the recording already is the target instrument,
  so separation is skipped — much faster (it's the slowest stage) and more
  accurate (no separation artifacts).
- `--vocal-melody` — also transcribe the vocals stem and reduce it to a
  monophonic main-melody line (mix source only). Guitar: written as an extra
  `song.melody.gp5` + `.melody.tab.txt`. Piano: added as its own melody track
  inside `song.piano.mid` (a separate staff in MuseScore).
- Stems are reused across runs of the same song automatically; force a re-run
  with `--no-reuse-stems`.

Outputs land in `output/<song_stem>/`:

```
output/song/
  separated/htdemucs/song/{vocals,drums,bass,other}.wav
  midi/song_basic_pitch.mid
  song.gp5              (guitar tab)
  song.tab.txt          (plain-text 6-line tab -- see the tab-staff note below)
  song.melody.gp5       (with --vocal-melody)
  song.piano.mid        (piano mode)
  song.pdf              (if MuseScore was found)
```

## Fret assignment (the part that has no off-the-shelf tool)

Deciding *which string and fret* plays each note is this project's own code
(`src/scoreforge/tab.py`). It runs a **dynamic program over the whole song**:
each chord event contributes its top-K playable voicings (scored by fret span,
position, open strings, with a steep penalty past a hand's comfortable reach),
and transitions are charged for hand movement — so the optimizer looks *ahead*,
keeping your hand near where the next chord needs it instead of greedily
grabbing the lowest frets. The cost model follows
[noahbaculi/guitar-tab-generator](https://github.com/noahbaculi/guitar-tab-generator)'s
pathfinding weights (movement 100 : span 10 : position 1), plus an open-string
bonus of our own; the survey of alternatives (guitar_dp, chord-fingering, HMM
papers) is in `docs/research/fingering-survey.md`. It's pure Python and fully
unit-tested with synthetic notes — no audio needed:

```bash
python -m unittest discover -s tests -v
```

## Setup

### 1. Clone with submodules

```bash
git clone --recurse-submodules <this-repo-url>
cd ScoreForge
# if you already cloned without --recurse-submodules:
git submodule update --init --recursive
```

### 2. Create a Python 3.11 environment and install this package

**Requires Python 3.11.** Demucs pins `torchaudio<2.1,>=0.8` and Basic Pitch pins
`tensorflow<2.15.1,>=2.4.1` -- neither range has published wheels for Python 3.12+, so a
plain `python -m venv` on a 3.12 (or newer) interpreter will fail at step 3 below. Use
conda (or any other way of getting a 3.11 interpreter) to pin the version:

```bash
conda create -n scoreforge python=3.11 -y
conda activate scoreforge

pip install -e .    # this package + pretty_midi, PyGuitarPro, madmom-prebuilt,
                    # fastapi/uvicorn for the web GUI
```

### 3. Install the vendored pipeline tools (manual step)

The Demucs and Basic Pitch submodules under `external/` are not installed
automatically by pip -- install each in editable mode (same env):

```bash
pip install -e external/demucs
pip install -e external/basic-pitch
```

Both pull in large ML dependencies (PyTorch, TensorFlow) and, on first run, download
multi-GB pretrained model weights -- expect this to take a while and require several
GB of disk space.

**GPU note:** `pip install -e external/demucs` pulls plain `torch`/`torchaudio` from
PyPI, which on Windows means the **CPU-only** build, even if an NVIDIA GPU is present.
Demucs's `torchaudio<2.1` pin (needed for the Python 3.11 install to resolve at all --
see above) corresponds to a PyTorch release from mid-2023, which predates GPU
architectures like the RTX 50-series (Blackwell) -- even installing a CUDA build of
that old torch version would likely lack compiled kernels for such a new GPU. So on
a Blackwell-class card, CPU is the only realistic option here without a much larger,
riskier surgery (forcing a newer torch than Demucs's pin supports). Expect `htdemucs`
(a transformer model) to take several minutes per track on CPU -- the progress bar can
sit near 0% during the first chunk; that's normal, not a hang. `run_pipeline.py`
defaults `--jobs` to `min(4, cpu_count)` to parallelize chunk processing across cores;
raise or lower it with `--jobs N` (each job holds a model copy in RAM, so don't set it
past your memory budget). Tip: `--source solo` skips Demucs entirely when the
recording is already solo guitar/piano.

### 4. (Optional) Install MuseScore 4

Only needed for the final PDF-rendering stage. Install from
[musescore.org](https://musescore.org) and make sure `MuseScore4.exe` (Windows),
`mscore4portable.exe`, or `mscore` (Linux/other) is on your `PATH`. If it's not
found, the pipeline skips this stage with a message instead of failing -- the
`.gp5` file is still fully usable (open it in MuseScore, Guitar Pro, or TuxGuitar).

### 5. (Optional) Chord detection setup

`pip install madmom-prebuilt` (already in `requirements.txt`/`pyproject.toml`) installs
real prebuilt wheels of [madmom](https://github.com/CPJKU/madmom) for Python 3.11 --
plain `pip install madmom` pulls PyPI's 2018 release, which does not build on Windows.

That wheel has its own packaging bug: its dist-info folder is named
`madmom_prebuilt-*.dist-info`, but `madmom/__init__.py` looks itself up via
`importlib.metadata.distribution("madmom")` and crashes with `PackageNotFoundError` if
that exact name isn't registered. One-time fix per environment, after installing:

```bash
# adjust the version number to match what pip installed if it's changed since
SITE_PACKAGES="$(python -c "import site; print(site.getsitepackages()[0])")"
cp -r "$SITE_PACKAGES/madmom_prebuilt-0.17.post1.dist-info" "$SITE_PACKAGES/madmom-0.17.post1.dist-info"
sed -i 's/^Name: madmom-prebuilt$/Name: madmom/' "$SITE_PACKAGES/madmom-0.17.post1.dist-info/METADATA"
```

Verify with `python -c "import madmom; print('ok')"`. If madmom isn't installed (or
this fix hasn't been applied), the pipeline just skips chord detection with a printed
message -- tab generation still works, you just don't get chord/capo info.

Chord vocabulary is major/minor triads + "N" (no chord) only -- no 7ths/sus/dim.

### 6. (Optional) Piano-specialized transcription model

```bash
pip install piano-transcription-inference
```

Installs bytedance's high-resolution piano transcription model (MIT license;
MAESTRO onset F1 ~96.7% — see `docs/research/model-survey.md`), which piano mode
then uses automatically instead of the instrument-agnostic Basic Pitch. First
use downloads a ~165MB checkpoint. Without it, piano mode falls back to Basic
Pitch with a printed note.

## Chords, capo, dynamics, and the MuseScore tab-staff note

**Chords and capo (guitar mode):** if madmom is installed (setup 5), each measure
gets a chord label (e.g. `D#:maj`) detected directly from audio -- independent of the
note transcription -- shown as text above the first beat in `song.gp5`/`song.pdf` and
as a chord chart at the top of `song.tab.txt`. The pipeline also prints a suggested
capo position (tries frets 0-7, picks whichever lets the most detected chords be
played as open/easy shapes -- a fingering-convenience heuristic, not a claim about
what capo the original recording used) and flags measures where the transcribed notes
don't fit the detected chord well (marked with `?` in the .gp5 text) -- worth a second
look during manual correction, not a guaranteed error (the chord vocabulary is
triads-only, and real playing legitimately has passing tones). Skip with
`--skip-chords`.

All note velocities are written as a single constant value (forte). Basic Pitch's
transcribed per-note velocities fluctuate constantly, and in the Guitar Pro format
`Note.velocity` **is** the dynamic marking (16-unit bands map to pp/p/mp/mf/f/...) --
so real transcribed velocity produced a distracting new dynamic annotation on nearly
every note. We don't treat transcribed velocity as meaningful dynamics data anyway, so
it's normalized away instead.

**Tab staff note:** `song.gp5` correctly declares a tablature staff internally (verified
by parsing it back with PyGuitarPro), but MuseScore's own `.gp5` importer does not
create a visible TAB staff from that flag -- confirmed by converting our `.gp5` through
`MuseScore4.exe -o out.musicxml` and finding no tab staff in the result. This is a
MuseScore import limitation, not something fixable from our side via CLI. If you want
the PDF/MuseScore view itself to show a tab staff: open `song.gp5` in MuseScore, go to
**Edit → Instruments** (or right-click the staff → **Staff/Part Properties**), and
enable **Tablature** for the Guitar part, then re-export to PDF. `song.tab.txt` sidesteps
this entirely -- it's rendered directly from our own string/fret assignments, so it's
guaranteed to show as tab with no MuseScore involvement.

## Why not use MuseScore to generate the TAB itself?

MuseScore's CLI can do headless MIDI -> MusicXML/PDF conversion, but it **cannot**
programmatically create a TAB staff (string/fret notation) from that MIDI: there's
no CLI flag for it, the plugin API is too immature to script it reliably, and
MuseScore doesn't honor a MusicXML staff-type hint on import. Concretely, that means
the actual "which string, which fret" decision -- the whole point of a guitar TAB --
has to be made by code we control. So this project builds the `.gp5` tab directly
with `PyGuitarPro` (see `src/scoreforge/tab.py`), and only calls out to
MuseScore afterward, optionally, to render that already-complete tab to a PDF.

## Hosting the frontend on Cloudflare Pages (optional)

The conversion itself **cannot run on Cloudflare** — Workers have no Python/
PyTorch runtime (128 MB memory, no GPU), and Workers AI has no source-separation
or transcription models (full analysis: `docs/research/deployment-gui-survey.md`).
So the architecture is local-first: the engine on your machine serves its own
copy of the UI at `python -m scoreforge`.

The same static frontend can additionally be hosted on Cloudflare Pages as a
convenience URL — it detects it isn't running next to an engine and asks for
your local engine's address (`http://127.0.0.1:8765`), which browsers permit
because localhost is exempt from mixed-content blocking:

```bash
npx wrangler pages deploy src/scoreforge/static --project-name=scoreforge
```

CORS is already enabled on the engine for this. Note Chrome is rolling out a
Local Network Access prompt that adds one permission click to this pattern; the
locally-served UI (default) is unaffected.

## Status / what's verified vs. what needs real audio

The pure logic (fret assignment DP, melody reduction, chord parsing, tempo
correction, `.gp5` construction) is unit-tested with synthetic notes, and the
web GUI + solo-source pipeline path has been exercised end-to-end with synthetic
audio through the real Basic Pitch model. Full-mix runs (Demucs separation on
real songs, madmom chords, piano model) involve multi-GB models and minutes of
compute per song -- exercise those with a real recording after the submodule
installs (step 3).
