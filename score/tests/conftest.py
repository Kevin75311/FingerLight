"""Puts score/src on sys.path so ``import fingerlight_score`` works without an
install step (the module is stdlib-only, no build/packaging is needed yet)."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
