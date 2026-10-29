"""Stage 4 (optional): render a .gp5 tab to PDF via MuseScore.

MuseScore's CLI cannot programmatically *create* a TAB staff from MIDI (no CLI flag,
immature plugin API, doesn't honor MusicXML staff-type on import) -- that's why
tab.py builds the .gp5 directly with PyGuitarPro instead. MuseScore is only used
here, optionally, to render an already-complete .gp5 file to a PDF for printing/
reading. If MuseScore isn't installed, this stage is skipped gracefully (not an
error) -- the .gp5 file is still usable on its own (e.g. opened in Guitar Pro,
TuxGuitar, or MuseScore itself).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Executable names to probe for, in order. MuseScore 4's Windows install typically
# exposes "MuseScore4.exe"; the portable build uses "mscore4portable.exe"; Linux/older
# installs commonly expose "mscore" on PATH.
MUSESCORE_CANDIDATES = ("MuseScore4.exe", "mscore4portable.exe", "mscore")


def find_musescore() -> Optional[str]:
    """Return the path to a usable MuseScore executable, or None if not found."""
    for name in MUSESCORE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def render_pdf(input_path: Path, output_pdf: Optional[Path] = None) -> Optional[Path]:
    """Render ``input_path`` to a PDF using MuseScore, if it's installed.

    ``input_path`` is any file MuseScore's CLI can import -- a ``.gp5`` tab (guitar
    mode) or a plain ``.mid`` file (piano mode, which MuseScore imports directly into
    grand-staff notation since there's no .gp5 equivalent for absolute-pitch piano).

    Returns the output PDF path, or None (with a printed message) if MuseScore
    could not be found -- this is a soft skip, not an error, per the project spec.
    """
    musescore_exe = find_musescore()
    if not musescore_exe:
        print(
            "[render] MuseScore not found on PATH (looked for: "
            f"{', '.join(MUSESCORE_CANDIDATES)}). Skipping PDF render -- install "
            "MuseScore 4 (https://musescore.org) and ensure it's on PATH to enable "
            "this optional step. The .gp5/.mid tab/MIDI file is still available."
        )
        return None

    input_path = Path(input_path)
    output_pdf = Path(output_pdf) if output_pdf else input_path.with_suffix(".pdf")

    cmd = [musescore_exe, str(input_path), "-o", str(output_pdf)]
    print(f"[render] Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    return output_pdf
