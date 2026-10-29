"""Reader for the light-score format (v1).  The Part 2 side of the Part 2 <-> Part 3
contract defined in `lightscore/schema/lightscore-v1.schema.json`.

Deliberately stdlib-only and tolerant of nothing: a malformed score should fail here,
loudly, on the PC or at load time on the Pi -- never halfway through a practice session.
`jsonschema` is a PC-side dependency and is NOT assumed present on the Zero 2 W, so the
structural checks that matter at playback are re-implemented directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Sequence

MIDI_MIN = 21
MIDI_MAX = 108


class LightScoreError(ValueError):
    """Raised for any structurally invalid light-score."""


@dataclass(frozen=True)
class Note:
    """One cued note.  Field names mirror the schema's short keys for traceability."""

    t: int          # onset, ms from start
    d: int          # duration, ms
    p: int          # MIDI pitch, 21..108
    h: str          # hand, "L" or "R"
    f: int          # finger, 1=thumb .. 5=little
    stf: int = 0    # originating staff, 0 = unspecified
    v: int | None = None  # velocity, if the source had one

    @property
    def end(self) -> int:
        return self.t + self.d


@dataclass(frozen=True)
class Event:
    """All notes sharing one onset -- i.e. what the bar lights simultaneously.

    Practice advances event by event, not note by note: a chord is one thing to play.
    """

    t: int
    notes: tuple[Note, ...]

    @property
    def pitches(self) -> frozenset[int]:
        return frozenset(n.p for n in self.notes)


@dataclass(frozen=True)
class LightScore:
    meta: dict
    tempo: tuple[dict, ...]
    notes: tuple[Note, ...]
    events: tuple[Event, ...]

    @property
    def duration_ms(self) -> int:
        return max((n.end for n in self.notes), default=0)

    def __len__(self) -> int:
        return len(self.events)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise LightScoreError(msg)


def group_events(notes: Sequence[Note]) -> tuple[Event, ...]:
    """Group notes into simultaneous events by exact onset.

    Exact, not tolerant: the compiler already quantised onsets when it emitted the
    light-score, so any spread here is a *compiler* bug and should stay visible rather
    than be silently absorbed by a tolerance window.
    """
    buckets: dict[int, list[Note]] = {}
    for n in notes:
        buckets.setdefault(n.t, []).append(n)
    return tuple(
        Event(t=t, notes=tuple(sorted(buckets[t], key=lambda n: n.p)))
        for t in sorted(buckets)
    )


def parse(doc: dict) -> LightScore:
    """Validate and convert a parsed JSON document into a `LightScore`."""
    _require(doc.get("format") == "lightscore", "not a light-score (bad 'format')")
    _require(doc.get("version") == 1, "unsupported version: %r" % (doc.get("version"),))
    raw = doc.get("notes")
    _require(isinstance(raw, list), "'notes' must be a list")
    _require(len(raw) > 0, "light-score has no notes")

    notes: list[Note] = []
    for i, r in enumerate(raw):
        try:
            t, d, p, h, f = r["t"], r["d"], r["p"], r["h"], r["f"]
        except (KeyError, TypeError) as exc:
            raise LightScoreError("note %d is missing %s" % (i, exc)) from None
        _require(isinstance(t, int) and t >= 0, "note %d: bad onset %r" % (i, t))
        _require(isinstance(d, int) and d > 0, "note %d: bad duration %r" % (i, d))
        _require(
            isinstance(p, int) and MIDI_MIN <= p <= MIDI_MAX,
            "note %d: pitch %r outside the 88-key range %d..%d"
            % (i, p, MIDI_MIN, MIDI_MAX),
        )
        _require(h in ("L", "R"), "note %d: bad hand %r" % (i, h))
        _require(isinstance(f, int) and 1 <= f <= 5, "note %d: bad finger %r" % (i, f))
        notes.append(
            Note(t=t, d=d, p=p, h=h, f=f, stf=int(r.get("stf", 0)), v=r.get("v"))
        )

    notes.sort(key=lambda n: (n.t, n.p))
    return LightScore(
        meta=dict(doc.get("meta") or {}),
        tempo=tuple(doc.get("tempo") or ()),
        notes=tuple(notes),
        events=group_events(notes),
    )


def load(path: str) -> LightScore:
    with open(path, encoding="utf-8") as fh:
        return parse(json.load(fh))
