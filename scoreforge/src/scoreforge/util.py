"""Small shared helpers."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def find_executable(name: str) -> str:
    """Locate a CLI tool that was pip-installed into the current environment.

    ``shutil.which`` alone is not enough: when this process is launched via the
    environment's python.exe directly (e.g. the web GUI started from a shell
    where the conda env isn't activated), the env's ``Scripts``/``bin`` dir is
    not on PATH, so console scripts like ``demucs``/``basic-pitch`` aren't found
    even though they're installed right next to the interpreter. Look there too.
    """
    exe = shutil.which(name)
    if exe:
        return exe

    py_dir = Path(sys.executable).parent
    candidates = [
        py_dir / f"{name}.exe",            # Windows venv (python.exe in Scripts/)
        py_dir / name,                     # POSIX venv/conda (bin/)
        py_dir / "Scripts" / f"{name}.exe",  # Windows conda env root
        py_dir / "Scripts" / name,
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return name  # let subprocess raise a clear error with the bare name
