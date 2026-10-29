"""Note -> LED mapping for the PX-S1100 finger-cue bar.

This module exists to make one point precisely: **we do not fork upstream's LED driver.**

Piano-LED-Visualizer maps a MIDI note to a strip index in `lib/functions.py:528`
(`get_note_position`, verified at pin b502ad67). That function assumes a *uniform*
13.889 mm per note.  The real key-tail pitch on an 88-key board is 13.675 mm inside an
octave, and the ends are irregular: A0-A#0-B0 share two white-key widths, and C8 gets a
full white key to itself.  Upstream's uniform assumption therefore drifts, and at the top
of the board it asks for LED 176 on a 176-LED strip -- an index that does not exist.

The fix is not to rewrite the function.  It is to feed it the right `shift` and
`note_offsets`, which `tools/gen_map.py` derives analytically from the measured octave
span.  `verify_against_map()` below proves the two agree on all 88 notes, so the
correction is a *settings* change (decision D-08).

Nothing here imports `rpi_ws281x`, upstream, or anything Pi-specific: it is pure
arithmetic and runs (and is tested) on a PC with no hardware attached.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

# The 88-key range, A0..C8.  Anything outside cannot be shown on the bar.
MIDI_MIN = 21
MIDI_MAX = 108

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_MAP_CSV = os.path.join(_REPO_ROOT, "data", "led_map.csv")
DEFAULT_SETTINGS = os.path.join(
    _REPO_ROOT, "firmware", "config", "settings_overlay.json"
)


@dataclass(frozen=True)
class StripSettings:
    """The handful of upstream settings that define the mapping.

    These are exactly the values `tools/gen_map.py` emits, and exactly the values that
    go into Piano-LED-Visualizer's own `config/settings.xml`.  Keeping them in one
    frozen object means the overlay file, the tests and the firmware cannot disagree.
    """

    led_count: int = 176
    leds_per_meter: int = 144
    shift: int = -2
    note_offsets: tuple[tuple[int, int], ...] = (
        (21, -1), (51, 1), (96, 1), (107, -1))
    reverse: bool = False

    # Provenance -- the measurement this mapping was generated from.
    octave_span_mm: float = 164.8

    #: Per-key corrections from the calibration screen, as sorted (midi, leds) pairs:
    #: how many LEDs to move that key's pair, + toward C8.  Not an upstream setting --
    #: it is applied in `note_to_leds`, on top of upstream's formula, so
    #: `get_note_position` stays verbatim and `verify_against_map` still checks the
    #: generated map rather than the hand corrections.
    led_offsets: tuple[tuple[int, int], ...] = ()

    @classmethod
    def load(cls, path: str = DEFAULT_SETTINGS) -> "StripSettings":
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(
            led_count=int(raw["led_count"]),
            leds_per_meter=int(raw["leds_per_meter"]),
            shift=int(raw["shift"]),
            note_offsets=tuple((int(a), int(b)) for a, b in raw["note_offsets"]),
            reverse=bool(raw.get("reverse", False)),
            octave_span_mm=float(raw.get("octave_span_mm", 0.0)),
        )

    def with_offsets(self, offsets: dict[int, int]) -> "StripSettings":
        """A copy carrying these per-key corrections (zeros dropped)."""
        pairs = tuple(sorted((int(n), int(d)) for n, d in offsets.items() if int(d)))
        return replace(self, led_offsets=pairs)

    def offset_for(self, note: int) -> int:
        for n, d in self.led_offsets:
            if n == note:
                return d
        return 0


def get_note_position(note: int, s: StripSettings) -> int:
    """Upstream's `lib/functions.py:528`, transcribed verbatim at pin b502ad67.

    Kept byte-for-byte faithful on purpose: this is the function that will actually run
    on the Pi, so our tests must exercise *its* behaviour, not a cleaned-up rewrite of
    it.  If upstream changes this function, this copy is what tells us.
    """
    note_offset = 0
    for threshold, offset in s.note_offsets:
        if note > threshold:
            note_offset += offset
    note_offset -= s.shift
    density = s.leds_per_meter / 72
    note_pos_raw = int(density * (note - 20) - note_offset)
    if s.reverse:
        return max(0, s.led_count - note_pos_raw)
    return max(0, note_pos_raw)


def note_to_leds(note: int, s: StripSettings) -> tuple[int, int]:
    """The **2-LED pair** for a key: (hand_pixel, finger_pixel).  Decision D-12.

    Ten classes (5 fingers x 2 hands) are not distinguishable as ten hues in peripheral
    vision, and at 144 LED/m each key owns about 1.97 LEDs -- so the roles are split
    across the pair rather than encoded in one colour.  Left pixel carries the hand,
    right pixel carries the finger hue.

    The pair is clamped into the strip so the top and bottom keys, whose key widths are
    irregular, can never index off the end.  A per-key calibration offset
    (`StripSettings.led_offsets`) moves the pair before the clamp.
    """
    primary = get_note_position(note, s) + s.offset_for(note)
    lo = max(0, min(primary, s.led_count - 1))
    hi = max(0, min(primary + 1, s.led_count - 1))
    return lo, hi


def build_table(s: StripSettings) -> dict[int, int]:
    """Precompute the whole 88-note map once, at startup.

    The realtime path then does a dict lookup instead of arithmetic per MIDI event.
    """
    return {n: get_note_position(n, s) for n in range(MIDI_MIN, MIDI_MAX + 1)}


# --------------------------------------------------------------------------- checks


@dataclass
class MapCheck:
    """Result of checking settings against the generated map."""

    checked: int = 0
    mismatches: list[tuple[int, str, int, int]] = field(default_factory=list)
    out_of_range: list[tuple[int, int]] = field(default_factory=list)
    duplicate_leds: dict[int, list[int]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not (self.mismatches or self.out_of_range or self.duplicate_leds)

    def summary(self) -> str:
        return (
            "checked=%d mismatches=%d out_of_range=%d duplicate_leds=%d -> %s"
            % (
                self.checked,
                len(self.mismatches),
                len(self.out_of_range),
                len(self.duplicate_leds),
                "OK" if self.ok else "FAIL",
            )
        )


def load_map_rows(path: str = DEFAULT_MAP_CSV) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def verify_against_map(
    s: StripSettings, rows: Sequence[dict] | None = None
) -> MapCheck:
    """Does upstream's formula, fed these settings, reproduce `data/led_map.csv`?

    This is the acceptance test for decision D-08, and the reason the correction can
    ship as settings rather than as a driver fork.  It also catches the two failure
    modes that matter on real hardware:

    * an index at or past `led_count` (upstream's defaults do this for C8), and
    * two notes landing on the same primary LED, which would make them indistinguishable.
    """
    rows = load_map_rows() if rows is None else rows
    chk = MapCheck()
    seen: dict[int, list[int]] = {}
    for row in rows:
        midi = int(row["midi"])
        want = int(row["primary_led"])
        got = get_note_position(midi, s)
        chk.checked += 1
        if got != want:
            chk.mismatches.append((midi, row["name"], want, got))
        if not (0 <= got < s.led_count):
            chk.out_of_range.append((midi, got))
        seen.setdefault(got, []).append(midi)
    chk.duplicate_leds = {led: ns for led, ns in seen.items() if len(ns) > 1}
    return chk


def to_upstream_settings_xml(s: StripSettings) -> str:
    """The fragment to paste into Piano-LED-Visualizer's `config/settings.xml`.

    Emitted rather than hand-typed so the values can never drift from the generated map
    -- the same reason `gen_map.py` prints them instead of us copying them by eye.
    """
    offsets = json.dumps([list(p) for p in s.note_offsets])
    return (
        "<led_count>%d</led_count>\n"
        "<leds_per_meter>%d</leds_per_meter>\n"
        "<shift>%d</shift>\n"
        "<note_offsets>%s</note_offsets>\n"
        "<reverse>%d</reverse>\n"
        % (s.led_count, s.leds_per_meter, s.shift, offsets, int(s.reverse))
    )


if __name__ == "__main__":  # pragma: no cover - operator convenience
    settings = StripSettings.load()
    print("settings :", settings)
    print("verify   :", verify_against_map(settings).summary())
