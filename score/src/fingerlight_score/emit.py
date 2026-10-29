"""Write lightscore v1 (``lightscore/schema/lightscore-v1.schema.json``) from
:class:`~fingerlight_score.notes.FingeredNote`\\ s.

Field mapping is direct -- lightscore's short keys (t/d/p/h/f/stf/v) are exactly
``FingeredNote``'s t_ms/d_ms/pitch/hand/finger/staff/velocity, chosen that way in
``notes.py`` for exactly this reason.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

from .notes import FingeredNote


def to_lightscore(fingered: Sequence[FingeredNote], meta: Optional[Dict] = None) -> dict:
    """Build a lightscore v1 dict from fingered notes.

    Notes are emitted sorted by onset (``t``) then pitch, per the schema's
    "sorted by onset t ascending" contract. ``v`` (velocity) is omitted per-note
    when the source note didn't carry one -- the schema marks it optional.
    If ``meta`` is given and doesn't already say how the fingering was produced,
    ``meta.fingering`` defaults to ``"dp"`` (this module's whole reason to exist);
    pass ``meta={"fingering": "manual", ...}`` etc. to override. ``meta=None``
    omits the (optional) ``meta`` key entirely.
    """
    notes_json: List[dict] = []
    for fn in sorted(fingered, key=lambda n: (n.t_ms, n.pitch)):
        entry = {
            "t": fn.t_ms,
            "d": fn.d_ms,
            "p": fn.pitch,
            "h": fn.hand,
            "f": fn.finger,
            "stf": fn.staff,
        }
        if fn.velocity is not None:
            entry["v"] = fn.velocity
        notes_json.append(entry)

    doc: dict = {"format": "lightscore", "version": 1, "notes": notes_json}
    if meta is not None:
        doc["meta"] = dict(meta)
        doc["meta"].setdefault("fingering", "dp")
    return doc


def write_lightscore(
    path: Union[str, os.PathLike], fingered: Sequence[FingeredNote], meta: Optional[Dict] = None
) -> dict:
    """``to_lightscore`` then write it to ``path`` as JSON. Returns the dict written.

    Writes via a temp file + ``os.replace`` (never truncate-in-place) so a failure
    partway through never leaves a half-written or zero-byte file at ``path``.
    """
    doc = to_lightscore(fingered, meta)
    target = Path(path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return doc
