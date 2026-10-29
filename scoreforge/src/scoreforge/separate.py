"""Stage 1: source separation via Demucs.

Shells out to the ``demucs`` CLI (installed via ``pip install -e external/demucs``,
see README) rather than importing it as a library, so this module has no hard
dependency on demucs/torch just to be imported.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Tuple

from .util import find_executable

STEM_NAMES = ("vocals", "drums", "bass", "other")

# Per the workflow doc, we don't run a dedicated guitar-isolation model (AudioSep is
# out of scope for the "simple path") -- the "other" stem is the guitar approximation.
GUITAR_STEM_NAME = "other"

# Piano needs a dedicated stem -- htdemucs's default 4-stem output has no piano stem
# (piano bleeds into "other" along with guitar), so piano mode requires switching to
# the already-vendored 6-stem model instead.
PIANO_STEM_NAME = "piano"

# Per-model stem name tuples. Only models whose stems differ from the default
# 4-stem STEM_NAMES need an entry here.
STEM_NAMES_BY_MODEL: Dict[str, Tuple[str, ...]] = {
    "htdemucs_6s": ("vocals", "drums", "bass", "other", "guitar", "piano"),
}


def separate(
    song_path: Path, output_dir: Path, model: str = "htdemucs", jobs: int = 0,
    reuse_existing: bool = True,
) -> Dict[str, Path]:
    """Run Demucs on ``song_path``, writing stems under ``output_dir``.

    ``jobs`` maps to Demucs's ``-j/--jobs`` flag: number of worker threads used to
    process chunks in parallel. Demucs defaults to 0 (serial). On CPU-only installs
    (no CUDA), htdemucs's transformer is slow -- a few minutes per track is normal,
    not a hang -- and raising ``jobs`` to use more CPU cores is the main lever
    available without a GPU (each job holds a model copy in memory, so don't set it
    higher than you have RAM for).

    ``reuse_existing``: separation is by far the slowest stage and is deterministic
    for a given (song, model), so if every expected stem .wav already exists from a
    prior run, skip Demucs entirely and return the existing paths. Pass ``False``
    to force a re-run (e.g. after changing the input file in place).

    Returns a dict mapping stem name ("vocals", "drums", "bass", "other") to the
    .wav file path Demucs produced.
    """
    song_path = Path(song_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stem_dir = output_dir / model / song_path.stem
    stem_names = STEM_NAMES_BY_MODEL.get(model, STEM_NAMES)
    expected = {name: stem_dir / f"{name}.wav" for name in stem_names}
    if reuse_existing and all(p.exists() for p in expected.values()):
        print(f"[separate] Reusing existing stems under {stem_dir} (delete the "
              f"directory or pass reuse_existing=False to force a re-run).")
        return expected

    demucs_exe = find_executable("demucs")
    cmd = [demucs_exe, "-n", model, "-o", str(output_dir)]
    if jobs:
        cmd += ["-j", str(jobs)]
    cmd.append(str(song_path))
    print(f"[separate] Running: {' '.join(cmd)}")
    print(
        "[separate] Note: on CPU-only PyTorch, htdemucs can take several minutes for "
        "a full-length track. The progress bar may sit near 0% for a while during the "
        "first chunk -- that's normal, not a hang."
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    subprocess.run(cmd, check=True, env=env)

    # Demucs writes to <output_dir>/<model>/<song_stem>/<stem_name>.wav
    stem_dir = output_dir / model / song_path.stem
    stem_names = STEM_NAMES_BY_MODEL.get(model, STEM_NAMES)
    stems = {name: stem_dir / f"{name}.wav" for name in stem_names}

    missing = [name for name, path in stems.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"Demucs did not produce expected stem(s) {missing} under {stem_dir}. "
            f"Check that demucs is installed (pip install -e external/demucs) and ran "
            f"without errors."
        )
    return stems
