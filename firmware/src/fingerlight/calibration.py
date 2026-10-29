"""Calibration mode (decision D-18).

The note->LED map is derived analytically from one measured number, M1 (the octave span).
That is only as good as the measurement and as the bar's physical placement, and neither
is knowable from software.  So: light one LED, ask which key it actually sits over, three
times, at A0 / C4 / C8.  Three anchors across the whole board pin the map empirically and
beat trusting any spec sheet.

What three anchors buy you
--------------------------
Two things can be wrong, and they are **distinguishable**, which is the entire reason for
using three anchors instead of one:

* **Translation** -- the bar is glued a few millimetres left or right of where the map
  assumes.  Every anchor is off by the *same* distance.  Fix: one integer ``shift``.
* **Scale** -- M1 itself is wrong, so the map's key pitch is wrong.  The error grows
  linearly across the board: near zero at one end, largest at the other.  Fix: re-run
  ``tools/gen_map.py`` with a corrected octave span.  A ``shift`` cannot fix this, and
  applying one would just move the error from one end of the keyboard to the other.

``solve()`` fits ``error_mm = a + b * position_mm`` over the anchors and reports both:
``a`` is the translation, ``b`` the fractional scale error.  It then says plainly which
correction is called for, rather than silently applying a shift that hides a bad M1.

Pure arithmetic -- no LEDs, no MIDI, no prompts.  The caller drives the UI.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Sequence

from .notemap import DEFAULT_MAP_CSV, StripSettings, get_note_position

#: The three anchors: bottom, middle, top.  Spread matters more than count -- two notes
#: near each other cannot separate translation from scale.
DEFAULT_ANCHORS: tuple[int, ...] = (21, 60, 108)  # A0, C4, C8

#: Below this, a residual is not worth chasing: it is under the quantisation floor of a
#: 144/m strip (half the 6.944 mm LED pitch).
NEGLIGIBLE_MM = 3.5

#: A fractional scale error below this is within the noise of a single hand measurement.
NEGLIGIBLE_SCALE = 0.0015  # 0.15%, i.e. ~0.25 mm on a 164.8 mm octave


@dataclass(frozen=True)
class Anchor:
    """One calibration response.

    ``expected_note`` is what our map believes sits under ``led``; ``actual_note`` is the
    key the player actually pressed when that LED lit.  Equal means the map is right at
    this point.
    """

    led: int
    expected_note: int
    actual_note: int


@dataclass
class Correction:
    translation_mm: float
    scale_error: float           # fractional; +0.01 means the map's spacing is 1% small
    suggested_shift: int         # integer LEDs, to hand to settings_overlay.json
    suggested_octave_span_mm: float | None
    residuals_mm: tuple[float, ...]
    verdict: str

    @property
    def needs_remeasure(self) -> bool:
        return self.suggested_octave_span_mm is not None


def load_key_centres(path: str = DEFAULT_MAP_CSV) -> dict[int, float]:
    """MIDI note -> key-tail centre in mm, from the generated map."""
    with open(path, encoding="utf-8", newline="") as fh:
        return {int(r["midi"]): float(r["tail_centre_mm"]) for r in csv.DictReader(fh)}


@dataclass
class CalibrationSession:
    """Drives the anchors one at a time and collects responses."""

    settings: StripSettings
    anchors: tuple[int, ...] = DEFAULT_ANCHORS
    responses: list[Anchor] = field(default_factory=list)
    index: int = 0

    @property
    def finished(self) -> bool:
        return self.index >= len(self.anchors)

    @property
    def current_note(self) -> int | None:
        return None if self.finished else self.anchors[self.index]

    def current_led(self) -> int | None:
        """The LED to light for the current anchor."""
        note = self.current_note
        return None if note is None else get_note_position(note, self.settings)

    def confirm(self, actual_note: int) -> None:
        """Record which key the player says the lit LED sits over."""
        if self.finished:
            raise RuntimeError("calibration already complete")
        expected = self.anchors[self.index]
        led = get_note_position(expected, self.settings)
        self.responses.append(
            Anchor(led=led, expected_note=expected, actual_note=actual_note)
        )
        self.index += 1

    def reset(self) -> None:
        self.responses.clear()
        self.index = 0

    def solve(self, centres: dict[int, float] | None = None) -> Correction:
        return solve(self.responses, self.settings, centres)


def solve(
    responses: Sequence[Anchor],
    settings: StripSettings,
    centres: dict[int, float] | None = None,
) -> Correction:
    """Fit ``error_mm = a + b * position_mm`` across the anchors.

    Least squares on two parameters; with three well-spread anchors this is
    over-determined by one, and the residuals are what tell you whether the linear model
    fits at all (it will not, if a response was simply mis-entered).
    """
    if len(responses) < 2:
        raise ValueError("need at least 2 anchors to separate translation from scale")
    centres = load_key_centres() if centres is None else centres

    xs, ys = [], []
    for r in responses:
        try:
            expected_mm = centres[r.expected_note]
            actual_mm = centres[r.actual_note]
        except KeyError as exc:
            raise ValueError("note %s is not in the map" % exc) from None
        # The LED sits over `actual`, but the map placed it over `expected`; the map is
        # therefore displaced by this much at this point on the board.
        xs.append(expected_mm)
        ys.append(actual_mm - expected_mm)

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("anchors are all at the same position; spread them out")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = mean_y - b * mean_x

    residuals = tuple(y - (a + b * x) for x, y in zip(xs, ys))

    mm_per_led = 1000.0 / settings.leds_per_meter
    # A positive translation means the map is left of the bar, so LEDs must move right.
    suggested_shift = int(round(-a / mm_per_led))

    scale_significant = abs(b) > NEGLIGIBLE_SCALE
    suggested_span = (
        round(settings.octave_span_mm * (1.0 + b), 2) if scale_significant else None
    )

    worst = max((abs(v) for v in ys), default=0.0)
    if worst <= NEGLIGIBLE_MM and not scale_significant:
        verdict = (
            "Map is good: worst anchor error %.1f mm is within the %.1f mm "
            "quantisation floor. No change needed." % (worst, NEGLIGIBLE_MM)
        )
    elif scale_significant:
        verdict = (
            "SCALE error of %+.2f%% -- the octave span is wrong, not the placement. "
            "Re-run `python tools/gen_map.py %.2f` and update settings_overlay.json. "
            "A shift cannot fix this; it would only move the error to the other end "
            "of the keyboard." % (b * 100.0, suggested_span)
        )
    else:
        verdict = (
            "TRANSLATION of %+.1f mm -- the bar is offset from where the map assumes. "
            "Set shift = %d in settings_overlay.json (currently %d)."
            % (a, settings.shift + suggested_shift, settings.shift)
        )

    return Correction(
        translation_mm=a,
        scale_error=b,
        suggested_shift=suggested_shift,
        suggested_octave_span_mm=suggested_span,
        residuals_mm=residuals,
        verdict=verdict,
    )
