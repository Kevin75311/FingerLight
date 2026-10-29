---
name: model-survey
aliases: [Model survey]
tags: [research, score]
---

# Model survey: alternatives/additions to Demucs + Basic Pitch

Researched 2026-07-11. Scope: is there a measurable, Windows+Python-3.11-installable
upgrade over the current stack (Demucs `htdemucs`/`htdemucs_6s` + Spotify Basic Pitch)
for guitar transcription, piano transcription, vocal melody extraction, and source
separation; plus a light scan of melody->accompaniment generation.

All web findings below came from WebSearch summaries (marked "search summary") or
direct WebFetch/`curl` of primary sources (marked "verified: <url>"). Where a claim
is only a search-engine paraphrase of a paper/README I have not independently opened
the primary doc, I say so.

---

## 1. Guitar transcription (polyphonic, from a separated guitar/"other" stem)

| Option | Accuracy evidence | Windows + py3.11 install | License | Integration effort |
|---|---|---|---|---|
| **Basic Pitch (current)** | 79% F1 (Fno, no-offset) on GuitarSet per one search summary of the Basic Pitch paper/HF card; a different benchmark comparison (unnamed test set) reports 54.6% P / 85.0% R / 66.1% F1 at 50ms resolution. **Conflict, not resolved** — different eval protocols, neither independently re-verified. | Already installed (verified — it's the current stack). | Apache-2.0 (Spotify). | None — baseline. |
| **trimplexx/music-transcription** (CRNN, GuitarSet-trained) | Repo description states "Achieves 0.87 MPE F1-Score on GuitarSet" (search summary claim, also visible verbatim in the GitHub repo description via `gh api` — verified: https://github.com/trimplexx/music-transcription). MPE (multi-pitch estimation), not full tab/note-with-string metric — not directly comparable to Basic Pitch's Fno number. | Not evaluated — small/low-star research repo, no PyPI package, no install docs found. Assumed pip-installable only via manual `git clone` + reading source. | **No LICENSE file** (verified via GitHub API: `license: None`) — legally unclear for reuse. | High — would need to read source to find a CLI/inference entrypoint; likely research-grade, undocumented. |
| **FretNet** (`cwitkowitz/guitar-transcription-continuous`, ICASSP 2023) | Reports SOTA-at-the-time continuous-pitch-contour metrics on GuitarSet 6-fold CV (paper: arxiv.org/abs/2212.03023) — exact F1 not extracted from the paper itself (not opened), only the repo's own description of what it computes. | Install is `git clone` + `pip install -r requirements.txt` + `pip install -e .` (verified from README: https://github.com/cwitkowitz/guitar-transcription-continuous). Depends on the author's own `amt-tools` and `guitar-transcription-with-inhibition` repos, plus `sacred`/`tensorboard` for experiment tracking — this is training/eval research code, not a packaged inference tool. No Windows-specific notes found. Python version unstated. | MIT (verified via GitHub API). | High — no ready CLI, no pretrained-checkpoint-only inference path documented in the README; would need to adapt `six_fold_cv_scripts/experiment.py` for standalone inference. |
| **GAPS** (ISMIR 2024 benchmark model, SOTA on GuitarSet incl. zero-shot) | Paper claims SOTA on GuitarSet in supervised and zero-shot settings (search summary of arxiv.org/abs/2408.08653, not opened directly). No F1 number extracted. | Not evaluated — the **dataset** license found is explicitly non-commercial/no-redistribution ("may only be used by the individual signing... non-commercial research purposes... may not be sold, leased, published or distributed"), and no code-license info was found for the model itself. | Dataset: non-commercial only (verified via search summary of https://aim-qmul.github.io/GAPS/ license page). Code license: not found. | Not recommended to pursue given the licensing red flag alone. |
| **YourMT3 / YourMT3+** (transformer, multi-instrument incl. guitar, MT3 lineage) | Benchmarked on Slakh2100 / multi-instrument transcription leaderboards per PapersWithCode badges in the README (verified: raw README fetched, badges present) — no F1 number extracted from this pass. | Main GitHub repo (`mimbres/YourMT3`) is **89 KB** total (verified via GitHub API `size`), i.e. essentially a landing page — README has no installation section; the actual "Code" is linked as **"(pre-release)"** inside a GitHub issue comment, not a merged, documented package (verified: raw README fetched). Primary interfaces are a Colab notebook and a HuggingFace Spaces demo (GPU). No pip package, no Windows notes, no CPU-only guidance found. | GPL-3.0 (verified via GitHub API). | Very high / not practical — this is demo-oriented research code, not an installable library. |
| **Omnizart** (`music` mode covers piano/guitar-ish; general AMT toolkit) | Not guitar-specific; general multi-pitch model. No F1 number for guitar extracted. | `pip install omnizart` exists and was recently revived (releases jumped from 0.5.0 in 2021-12-09 straight to 0.6.0–0.6.3 on 2026-05-30/31 — **verified via raw `pypi.org/pypi/omnizart/json`**, `requires_python: >=3.8`). BUT `requires_dist` (same verified JSON) pins **`madmom>=0.16.1`** (real madmom, not `madmom-prebuilt`) and **`vamp>=1.1.0`** and **`pyfluidsynth>=1.2.5`**. This project's own CLAUDE.md already documents that real madmom "doesn't build on Windows/Python 3.11" — the exact reason this repo uses `madmom-prebuilt` instead. `vamp` additionally requires the Vamp plugin SDK, also a known Windows-build pain point. **Conclusion: install will very likely fail on Windows the same way real madmom already does for this project.** | MIT (verified via PyPI page). | Skip — same blocking dependency this project already worked around once. |

**Question 1 verdict:** No open-source guitar-specific model both beats Basic Pitch
*and* installs cleanly on Windows/py3.11 with reasonable effort. FretNet/GAPS/trimplexx
are research code with real accuracy claims but no packaged inference path (and GAPS has
a licensing blocker); YourMT3 is demo-only; Omnizart re-introduces the exact madmom
build problem this project already solved around. **Recommendation: keep Basic Pitch for
guitar; do not integrate any of these.**

---

## 2. Piano transcription

| Option | Accuracy | Windows + py3.11 | License | Effort |
|---|---|---|---|---|
| **bytedance/piano_transcription** (`piano_transcription_inference` on PyPI) | Paper (arxiv.org/abs/2010.01815, search-summarized, not opened directly) reports on MAESTRO test set: **onset F1 96.72%**, **onset+offset F1 82.47%**, **onset+offset+velocity F1 80.92%**, beating the prior Onsets-and-Frames onset F1 of 94.80%. No paper was found that benchmarks Basic Pitch and this system on the *same* test set/protocol — **not directly comparable**, flagged as a gap rather than resolved. Qualitatively: this is a piano-only specialist (trained solely on MAESTRO, regresses precise onset/offset times + pedal), vs. Basic Pitch's lightweight multi-instrument generalist (<20 MB, <17k params per its own engineering blog) — specialist-vs-generalist tradeoff. | `pip install piano_transcription_inference` (verified: PyPI page). Python "developed with 3.7", MIT, tiny package (~15 KB — logic only, weights fetched separately from Zenodo on first run, size not stated on PyPI). **README explicitly states "This repo supports Linux and Mac... Windows has not been tested"** (verified: PyPI page text quoting the repo). One GitHub issue titled "Piano transcription on Windows 10" exists (github.com/bytedance/piano_transcription/issues/3, not opened) suggesting some users got it working, but it's unofficial/unverified. A community GUI wrapper, **azuwis/pianotrans**, packages it specifically for Windows — not evaluated further. | MIT (verified). | Medium — pure PyTorch + librosa + ffmpeg, no exotic native deps, so the untested-Windows claim is likely just "nobody wrote the test," not a hard blocker (assumed, not verified). Worth a trial install in the existing conda env. |

**Question 2 verdict:** Best candidate found for a real upgrade. **Recommendation:
prototype it** for the `--instrument piano` path — pure-PyTorch dependency profile is
low-risk for Windows even though upstream never officially tested it, and its
MAESTRO-benchmarked onset F1 (96.72%) is a specialist result Basic Pitch's generalist
design isn't aiming to beat. Do a real install+run smoke test before committing (per
this project's own "no done without evidence" norm) since Windows support is explicitly
unverified upstream.

---

## 3. Vocal melody extraction (monophonic, from a Demucs vocals stem)

| Option | Output type | Windows + py3.11 | Notes |
|---|---|---|---|
| **torchcrepe** | Raw f0 track (Hz) + per-frame voicing confidence; monophonic only (verified via search summary of README + confirmed no polyphony support mentioned anywhere). | **Verified via raw PyPI JSON** (`pypi.org/pypi/torchcrepe/json`): `requires_python: None` (no restriction), deps are just `librosa, resampy, scipy, torch, torchaudio, tqdm`, MIT license. Since this project's env already has PyTorch (via Demucs), this is a near-zero-friction addition on Windows/py3.11. | Needs a separate step to turn f0 into discrete notes for tab/notation use. |
| **crepe-notes** (`xavriley/crepe_notes`, ICASSP-adjacent, arxiv.org/abs/2311.08884) | Converts a CREPE/torchcrepe f0 track into discrete MIDI notes (onset/pitch/duration + velocity), i.e. exactly the f0->note-events step needed. `pip install crepe-notes[crepe]` (search summary of PyPI page, not independently re-verified via raw JSON this pass). | Not independently verified this pass. **One documented gotcha (search summary):** its repeated-note-onset fallback depends on `madmom`, and "the PyPI version of madmom has a known compatibility issue with Python 3.10+" — the maintainer's workaround is `pip install git+https://github.com/CPJKU/madmom@main`, i.e. building real madmom from source. This is the same madmom-on-Windows risk flagged in section 1/CLAUDE.md. Only the repeated-note path needs it, so it may be avoidable/optional — not confirmed. | MIT-style OSS (not independently confirmed license this pass). |
| **RMVPE** | f0 track, 100 fps, ~32–1975 Hz range (search summary of README). Designed for *polyphonic* input (vocal-pitch-in-a-mix), which is actually a better fit than CREPE if not running RMVPE post-Demucs-isolation. | A Python-3.8–3.11-compatible, pip-installable fork exists: `CircuitCM/RVC-inference` (search summary claims "Python 3.8-3.11 compatible, pip installable" — not independently re-verified via raw PyPI/GitHub this pass). The canonical `Dream-High/RMVPE` repo is weights+code, not a standalone PyPI package. | Mainly distributed bundled inside RVC (voice-conversion) tooling, not as a standalone melody-extraction library — integration would mean pulling in RVC's dependency surface for one submodule. |
| **Basic Pitch on vocals** | Already-integrated tool, applied to the isolated vocals stem instead of guitar — would give note events directly (its native output), skipping the f0-to-note step entirely. | Zero new install cost — already in the stack. | Not benchmarked here specifically for *melody* (single-line) extraction accuracy vs. CREPE-based approaches; **not answerable from available sources** in this pass — no paper/benchmark comparing Basic Pitch vs. CREPE+crepe-notes specifically on solo/mixed vocal melody was found. |
| **Essentia / Melodia (`PredominantPitchMelodia`)** | f0 contour of the predominant melody, works on polyphonic input too (search summary of Essentia docs). | **Verified via raw `pypi.org/pypi/essentia/json`: the current PyPI release (2.1b6.dev1438) ships wheels only for `manylinux2014_x86_64` and macOS (cp314 arm64/x86_64) — no Windows wheel exists.** Installing on Windows would require building the C++ library from source, which is a much heavier lift than any other option here. | Not recommended given the confirmed absence of a Windows wheel. |

**Question 3 verdict:** **Recommendation:** `torchcrepe` (verified clean Windows/py3.11
install, reuses the existing PyTorch dependency) for the f0 track, piped into
`crepe-notes` for note segmentation *if* its madmom dependency turns out to be avoidable
in practice (needs a real install trial — treat as unverified until tried, per this
project's own madmom scar tissue). Skip Essentia/Melodia outright (no Windows wheel,
confirmed). RMVPE is a fallback worth a quick trial only if torchcrepe's monophonic
assumption proves too fragile on real vocal takes (breathiness/vibrato), since RMVPE is
built for a harder (polyphonic-context) version of the same problem.

---

## 4. Source separation upgrades over Demucs htdemucs / htdemucs_6s

| Option | SDR evidence | Windows + py3.11 / CPU | License |
|---|---|---|---|
| **BS-RoFormer / Mel-Band RoFormer** (via `ZFTurbo/Music-Source-Separation-Training`, weights from viperx/KimberleyJensen) | Search-summary figures (sources not independently opened as papers): "KimberleyJensen MelBand RoFormer... highest vocal SDR (10.98)... viperx BS RoFormer... 10.87"; separately, "going from htdemucs to BS-RoFormer gets you roughly +1.3 dB"; separately again, on an unnamed "Multisong Dataset," "HT Demucs achieves SDR vocals: 8.78, [vs] Mel Band RoFormer... 8.42" (Mel-Band *worse* here). **These three figures conflict with each other on relative ranking and come from different eval sets — reported as a conflict, not resolved.** A cleaner data point: BS-RoFormer trained on MUSDB18HQ+500 extra songs reportedly hit **11.99 dB average SDR** and won SDX 2023 (search summary of the BS-RoFormer arxiv paper, not opened). All figures are **vocals/general-stems SDR — none of the found figures are specifically for guitar or piano stems**, which is what this project actually needs; not answerable from available sources for guitar/piano specifically. | Repo confirmed MIT-adjacent territory: **verified via WebFetch of the repo — MIT license**, and a `--force_cpu` inference flag exists (CPU-only supported, verified). No CPU speed numbers found (not answerable from available sources) — transformer-based separation is typically much slower than Demucs on CPU, this is an assumption, not verified. | MIT (verified). |
| **`nomadkaraoke/python-audio-separator`** (packaged wrapper around UVR + MDX-Net + BS-Roformer/Mel-Band-Roformer models, incl. `htdemucs_6s`) | Bundles specific checkpoints with stated vocal SDR in filenames/config, e.g. `model_bs_roformer_ep_317_sdr_12.9755` (~12.98 dB vocals SDR) and Mel-Band variants ~12.4–12.6 dB (search summary of repo docs) — again **vocals-only figures, not guitar/piano**. | **Verified via raw PyPI JSON**: `requires_python: >=3.10` (so py3.11 is fine), MIT license. GitHub README explicitly documents a **CPU-only install path** and a Windows **DirectML** GPU-acceleration path (`pip install "audio-separator[dml]"`) — the most concretely Windows-documented option found in this whole survey. Ships `htdemucs_6s.yaml` (the same 6-stem model already in this project's plan) alongside the newer Roformer models, so it's a strict superset, not a replacement requiring migration risk. | MIT (verified). |
| **UVR (Ultimate Vocal Remover) model zoo directly (GUI)** | Same underlying models as `audio-separator` above (which wraps UVR's checkpoints) — no separate figures. | Windows/Mac/Linux desktop app (search summary), MIT, "100% open source" — but it's a GUI, not a library; `audio-separator` is the pip-installable path to the same models. | MIT (per project's own credit requirement, verified). |

**Question 4 verdict:** No figure was found that is guitar/piano-stem-specific for any
RoFormer model — the entire SDR literature located here is vocals-vs-instrumental, which
doesn't directly answer whether `htdemucs_6s`'s guitar/piano stems specifically improve.
That said, `python-audio-separator` is the lowest-risk path to *try* BS-RoFormer/Mel-Band
models against the current `htdemucs_6s` output on a real song: verified py3.11+Windows
support, MIT license, CPU-only path documented, and it doesn't replace `separate.py`'s
current model, just adds an option. **Recommendation: worth a manual A/B trial** (listen
to the guitar/piano stem before/after on one real song) rather than blind integration,
since no source-separated-instrument-stem SDR numbers were found either way.

---

## 5. Light scan — melody -> accompaniment / style generation

- **Magenta / Magenta.js (Google)** — symbolic (MIDI-level) generation, e.g. MusicVAE/GrooVAE for accompaniment-style continuation. Google's own Magenta research repo is effectively archived/inactive as of recent years (not independently re-verified this pass) — **verdict: legacy, low-confidence pick for new integration, needs its own maintenance check before use.**
- **MusicGen (Meta, `audiocraft`)** — audio-domain text/melody-conditioned generation; a search summary of its own paper states it beat Riffusion/Mousai baselines on subjective quality (84.8 vs 80.5 out of 100, per the MusicGen paper's own reported numbers, not independently opened). **Verdict: credible and actively used, but generates full audio, not a symbolic accompaniment for guitar/piano notation — a poor fit for this project's TAB/MIDI pipeline without a further transcription pass on its output (i.e., would need to feed back through Basic Pitch).**
- **Riffusion** — Stable-Diffusion-on-spectrograms audio generation; per the same MusicGen paper comparison it's the weaker baseline. **Verdict: not competitive, skip.**
- Newer 2024–2026 research (SingSong, AnyAccomp, Diff-a-riff, LaDA-Band) exists per search results but none were opened — **not answerable from available sources** whether any is practically installable; flagging as a "look here if this becomes a real feature request" list, not a recommendation.

**Question 5 verdict:** Nothing here is recommended for integration now — this whole
direction (generative accompaniment) is a different product feature (composition, not
transcription) from this pipeline's current audio->tab/sheet scope. Treat as informational.

---

## Overall ranked recommendations

1. **Integrate / prototype now:** `piano_transcription_inference` (bytedance) for the
   `--instrument piano` path — best-evidenced accuracy upgrade, MIT, low-risk deps.
   Needs one real install+run smoke test on Windows since upstream never tested it.
2. **Integrate / prototype now:** `torchcrepe` for a new vocal-melody path (if/when this
   project adds a vocal-melody feature) — verified clean install, reuses existing
   PyTorch. Pair with `crepe-notes` for note segmentation, but verify its madmom
   dependency doesn't reintroduce the Windows build problem before committing.
3. **Worth a manual A/B trial, not a blind swap:** `python-audio-separator`
   (BS-RoFormer/Mel-Band-Roformer models) vs. current `htdemucs_6s`, specifically
   listening to the guitar/piano stems — no stem-specific SDR evidence exists either way.
4. **Skip:** Omnizart (real-madmom + vamp dependency, same Windows blocker this project
   already worked around), YourMT3 (demo/research code, no packaged install), GAPS
   (licensing), FretNet/trimplexx (no packaged inference path / no license).
5. **Skip / not in scope:** Essentia/Melodia (confirmed no Windows wheel), all
   accompaniment-generation projects (wrong product surface for this pipeline today).

---

## What was NOT read (full primary sources not opened, only search-engine summaries)

- The actual papers for: Basic Pitch (ISMIR/ICASSP paper), FretNet (arxiv 2212.03023),
  GAPS (arxiv 2408.08653), BS-RoFormer (arxiv 2309.02612), Mel-Band RoFormer
  (arxiv 2310.01809), MusicGen (arxiv 2306.05284), bytedance piano transcription
  (arxiv 2010.01815).
- `crepe-notes` and RMVPE-fork PyPI/GitHub pages were not independently re-verified via
  raw JSON/API the way omnizart, torchcrepe, audio-separator, and essentia were —
  their install claims rest on WebSearch summaries only.
- No guitar-stem-specific or piano-stem-specific SDR benchmark was found for any
  separation model (Demucs variants or RoFormer variants) — all located SDR numbers are
  vocals/instrumental-only.

## Verified vs. assumed (summary)

**Verified directly (raw PyPI JSON, GitHub API, or WebFetch of primary README/LICENSE):**
Omnizart's madmom/vamp/pyfluidsynth dependency and Windows-relevant release history;
torchcrepe's dependency list and unrestricted Python version; `audio-separator`'s
`>=3.10` Python requirement, MIT license, and documented CPU/DirectML paths;
`piano_transcription_inference`'s MIT license and explicit "Windows not tested" text;
essentia's total absence of Windows wheels; YourMT3's GPL-3.0 license and 89 KB repo
size (i.e., no real installable package in the main repo); FretNet's MIT license and
git-clone-based install; trimplexx's missing license.

**Assumed / not independently verified:** all cross-model F1/SDR comparison numbers
(each came from a WebSearch summary of a paper, not the paper itself, and several
directly conflict with each other — see sections 1 and 4); CPU inference speed for
RoFormer-family separation models; whether `piano_transcription_inference` actually
installs successfully on Windows/py3.11 in practice (only upstream's own "not tested"
disclaimer and one unopened GitHub issue were found); whether `crepe-notes`'s madmom
dependency is avoidable in the common code path.
