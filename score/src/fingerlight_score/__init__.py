"""fingerlight_score -- the PC-side score compiler (Part 3).

Pure Python, stdlib only (see module docstrings in ``fingering.py`` for why).
Public surface:

    from fingerlight_score.notes import Note, FingeredNote
    from fingerlight_score.fingering import assign_fingering, COST_WEIGHTS
    from fingerlight_score.hands import assign_hands, resolve_hand_mode
    from fingerlight_score.emit import to_lightscore, write_lightscore
"""

from .notes import FingeredNote, Note
from .fingering import assign_fingering, COST_WEIGHTS
from .hands import assign_hands, resolve_hand_mode
from .emit import to_lightscore, write_lightscore

__all__ = [
    "Note",
    "FingeredNote",
    "assign_fingering",
    "COST_WEIGHTS",
    "assign_hands",
    "resolve_hand_mode",
    "to_lightscore",
    "write_lightscore",
]
