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

The sustain pedal holds released keys lit (when `Look.sustain` is on) until the pedal
comes up, then they fade like any other release.

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
    sustain: bool = True                # the pedal holds released keys lit

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
    down: bool = True                   # the key is held
    released_at: float | None = None    # when the fade began; None while held/sustained


@dataclass
class FreePlay:
    """Which keys are lit, and how brightly, at any moment."""

    lit: dict[int, _Lit] = field(default_factory=dict)
    pedal: bool = False

    def note_on(self, pitch: int, velocity: int = 100, t: float = 0.0) -> None:
        self.lit[pitch] = _Lit(velocity=velocity)

    def note_off(self, pitch: int, t: float = 0.0, sustain: bool = True) -> None:
        n = self.lit.get(pitch)
        if n is None or not n.down:
            return
        n.down = False
        if not (self.pedal and sustain):
            n.released_at = t

    def set_pedal(self, down: bool, t: float = 0.0) -> None:
        if self.pedal and not down:
            for n in self.lit.values():
                if not n.down and n.released_at is None:
                    n.released_at = t
        self.pedal = down

    def clear(self) -> None:
        self.lit.clear()

    def level(self, pitch: int, t: float, look: Look) -> float:
        """0..1: 1 while held or sustained, then a quadratic fade to 0 over fade_ms."""
        n = self.lit.get(pitch)
        if n is None:
            return 0.0
        if n.released_at is None:
            if not n.down and not look.sustain:
                return 0.0          # sustain switched off while the pedal held it
            return 1.0
        if look.fade_ms <= 0:
            return 0.0
        x = (t - n.released_at) * 1000.0 / look.fade_ms
        lv = (1.0 - min(1.0, max(0.0, x))) ** 2
        return lv if lv >= DARK else 0.0

    def prune(self, t: float, look: Look) -> bool:
        """Forget keys that have faded out.  Returns whether anything was dropped."""
        gone = [p for p in self.lit if self.level(p, t, look) <= 0.0]
        for p in gone:
            del self.lit[p]
        return bool(gone)

    def fading(self, t: float, look: Look) -> bool:
        """Is any key mid-fade?  The caller re-renders at its frame rate while so."""
        return any(n.released_at is not None and 0.0 < self.level(p, t, look)
                   for p, n in self.lit.items())

    def frame(self, t: float, look: Look, strip: StripSettings) -> dict[int, RGB]:
        """LED index -> RGB.  Both LEDs of a key's pair take its colour; where two keys
        share an LED the brighter channel wins, so neighbours never cancel out."""
        pixels: dict[int, RGB] = {}
        for p in sorted(self.lit):
            lv = self.level(p, t, look)
            if lv <= 0.0:
                continue
            rgb = scale(look.color_for(p, self.lit[p].velocity), lv)
            for i in note_to_leds(p, strip):
                old = pixels.get(i)
                pixels[i] = rgb if old is None else tuple(max(a, b) for a, b in zip(old, rgb))
        return pixels
