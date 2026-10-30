"""Free play: the bar lights whatever you play, in colours you choose.

No score, no cursor, no right or wrong -- a key goes down, its LED pair lights; it comes
up, the pair fades.  This is the first thing to run on a newly built bar (it proves
MIDI -> map -> strip end to end with nothing else in the way) and the mode the bar sits
in whenever no score is open.

Four colour styles (`STYLES`):

* ``solid``    -- every key one colour.
* ``split``    -- one colour below the split key, another from it upward (two hands).
* ``rainbow``  -- the hue walks once along the keyboard, A0 red to C8 violet.
* ``velocity`` -- soft is cool and dim, hard is hot and bright.  The only style that
  reads velocity; the others light at full level however softly a key is played.

How a key's light moves, modelled on a struck string:

* **Strike** (`Look.strike`) -- a key lights at full and, while held, eases down over
  `DECAY_MS` to a glow: ``steady`` stays full, ``soft`` settles at 40 %, ``flash`` at 12 %.
* **Release** -- from wherever it is, a quadratic fade to dark over `fade_ms`.
* **Pedal** (`Look.pedal`) -- ``longer`` (default): while the sustain pedal is down,
  releases fade `PEDAL_FADE_X` times slower (at least `PEDAL_FADE_MIN_MS`); lifting it
  cuts every tail still glowing back to the normal fade, like dampers coming down.
  ``hold``: released keys stay lit until the pedal comes up.  ``off``: ignored.

Pure logic, like `practice.py`: no MIDI, no LEDs, no threads.  Time is passed in, so
the fades are tested exactly rather than slept for.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field

from .colors import BLACK_KEY_GAIN, is_black_key, scale
from .notemap import StripSettings, note_to_leds

RGB = tuple[int, int, int]

STYLES = ("solid", "split", "rainbow", "velocity")

#: Velocity ramp, soft -> hard: teal, amber, red.  Interpolated piecewise.
VELOCITY_RAMP: tuple[RGB, ...] = ((0, 200, 160), (255, 150, 0), (255, 20, 0))

#: The quietest a soft note gets in the velocity style -- dim, but never invisible.
VELOCITY_FLOOR = 0.30

#: Below one 8-bit step a fading key is dark: stop drawing it, so a fade cannot hang
#: on forever at an invisible level through float rounding.
DARK = 1.0 / 255

#: Strike: how a held key settles, and the level it settles to per `Look.strike`.
DECAY_MS = 1200
STRIKES = {"steady": 1.0, "soft": 0.40, "flash": 0.12}

#: Pedal "longer": the release fade under the pedal.
PEDAL_MODES = ("off", "longer", "hold")
PEDAL_FADE_X = 4
PEDAL_FADE_MIN_MS = 1500


def parse_hex(text: str) -> RGB:
    """"#rrggbb" -> (r, g, b).  Raises ValueError on anything else."""
    t = str(text).strip()
    if len(t) != 7 or t[0] != "#":
        raise ValueError("colour must be #rrggbb, got %r" % (text,))
    return (int(t[1:3], 16), int(t[3:5], 16), int(t[5:7], 16))


@dataclass(frozen=True)
class Look:
    """How free play draws.  The app builds one from its settings."""

    style: str = "solid"
    color: RGB = (255, 176, 96)         # solid; split: below the split key
    color2: RGB = (0, 230, 200)         # split: the split key and above
    split: int = 60                     # C4
    fade_ms: int = 400                  # 0 = off the instant the key is released
    strike: str = "soft"                # STRIKES: how a held key settles
    pedal: str = "longer"               # PEDAL_MODES

    @property
    def hold_floor(self) -> float:
        return STRIKES.get(self.strike, 1.0)

    def release_ms(self, pedal_down: bool) -> float:
        if pedal_down and self.pedal == "longer":
            return max(self.fade_ms * PEDAL_FADE_X, PEDAL_FADE_MIN_MS)
        return self.fade_ms

    def color_for(self, pitch: int, velocity: int) -> RGB:
        if self.style == "split":
            base = self.color if pitch < self.split else self.color2
        elif self.style == "rainbow":
            # 0.83 of the wheel, so the two ends of the keyboard are not both red
            h = (pitch - 21) / 87.0 * 0.83
            r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
            base = (int(r * 255), int(g * 255), int(b * 255))
        elif self.style == "velocity":
            v = max(0.0, min(1.0, velocity / 127.0))
            seg = v * (len(VELOCITY_RAMP) - 1)
            i = min(int(seg), len(VELOCITY_RAMP) - 2)
            f = seg - i
            a, b = VELOCITY_RAMP[i], VELOCITY_RAMP[i + 1]
            base = tuple(int(round(a[c] + (b[c] - a[c]) * f)) for c in range(3))
            base = scale(base, VELOCITY_FLOOR + (1 - VELOCITY_FLOOR) * v)
        else:
            base = self.color
        return scale(base, BLACK_KEY_GAIN) if is_black_key(pitch) else base


@dataclass
class _Lit:
    velocity: int
    struck_at: float
    down: bool = True                   # the key is held
    released_at: float | None = None    # when the current fade began; None = not fading
    start: float = 1.0                  # the level that fade began from
    fade_s: float = 0.0                 # how long that fade lasts


@dataclass
class FreePlay:
    """Which keys are lit, and how brightly, at any moment."""

    lit: dict[int, _Lit] = field(default_factory=dict)
    pedal: bool = False

    def note_on(self, pitch: int, velocity: int = 100, t: float = 0.0) -> None:
        self.lit[pitch] = _Lit(velocity=velocity, struck_at=t)

    def note_off(self, pitch: int, t: float, look: Look) -> None:
        n = self.lit.get(pitch)
        if n is None or not n.down:
            return
        n.down = False
        if not (self.pedal and look.pedal == "hold"):
            self._fade(n, t, look)

    def set_pedal(self, down: bool, t: float, look: Look) -> None:
        """Pedal down lengthens every fade in progress; pedal up cuts them back.

        Each fade is re-anchored at its current level, so the light never jumps -- only
        the rate it is dimming at changes, like dampers lifting off or coming down."""
        if down == self.pedal:
            return
        self.pedal = down
        new_s = look.release_ms(down) / 1000.0
        for n in self.lit.values():
            if n.down:
                continue
            if n.released_at is None:
                if not down:                    # held by "hold": now it fades
                    self._fade(n, t, look)
            elif n.fade_s != new_s:             # fading: same level, new rate
                self._fade(n, t, look)

    def _fade(self, n: _Lit, t: float, look: Look) -> None:
        n.start = self._unfaded(n, t, look) if n.released_at is None else self._faded(n, t)
        n.released_at = t
        n.fade_s = look.release_ms(self.pedal) / 1000.0

    def clear(self) -> None:
        self.lit.clear()

    # ------------------------------------------------------------------- levels

    @staticmethod
    def _unfaded(n: _Lit, t: float, look: Look) -> float:
        """Held (or held by the pedal): full at the strike, easing to the strike floor."""
        floor = look.hold_floor
        x = min(1.0, max(0.0, (t - n.struck_at) * 1000.0 / DECAY_MS))
        return floor + (1.0 - floor) * (1.0 - x) ** 2

    @staticmethod
    def _faded(n: _Lit, t: float) -> float:
        if n.fade_s <= 0:
            return 0.0
        x = min(1.0, max(0.0, (t - n.released_at) / n.fade_s))
        lv = n.start * (1.0 - x) ** 2
        return lv if lv >= DARK else 0.0

    def level(self, pitch: int, t: float, look: Look) -> float:
        """0..1 for one key at time `t`."""
        n = self.lit.get(pitch)
        if n is None:
            return 0.0
        if n.released_at is not None:
            return self._faded(n, t)
        if not n.down and look.pedal != "hold":
            return 0.0              # was held by "hold", and the setting has changed
        return self._unfaded(n, t, look)

    def prune(self, t: float, look: Look) -> bool:
        """Forget keys that have faded out.  Returns whether anything was dropped."""
        gone = [p for p in self.lit if self.level(p, t, look) <= 0.0]
        for p in gone:
            del self.lit[p]
        return bool(gone)

    def frame(self, t: float, look: Look, strip: StripSettings) -> dict[int, RGB]:
        """LED index -> RGB.  Both LEDs of a key's pair take its colour; where two keys
        share an LED the brighter channel wins, so neighbours never cancel out."""
        pixels: dict[int, RGB] = {}
        for p in sorted(self.lit):
            lv = self.level(p, t, look)
            if lv <= 0.0:
                continue
            rgb = scale(look.color_for(p, self.lit[p].velocity), lv)
            if rgb == (0, 0, 0):
                continue
            for i in note_to_leds(p, strip):
                old = pixels.get(i)
                pixels[i] = rgb if old is None else tuple(max(a, b) for a, b in zip(old, rgb))
        return pixels
