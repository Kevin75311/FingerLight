"""Finger/hand -> RGB for the 2-LED pair.  Decisions D-12, D-13, D-17.

The encoding problem
--------------------
There are ten classes to show: five fingers on each of two hands.  Ten hues are not
reliably separable in *peripheral* vision, which is the only vision available while
sight-reading -- the player is looking at the music, not the bar.  So the roles are split
across the two LEDs each key owns at 144 LED/m (D-12):

    left pixel  -> which hand   (two classes; easy)
    right pixel -> which finger (five classes; the hue carries it)

Two constraints on the palette:

* **No pure blue** (D-13).  Blue is the dimmest channel per unit drive on these emitters
  and it dies against a matte black keytop.
* **Black keys read dimmer** (D-17), so their pixels get a gain.  The 1.4-1.7x figure is
  an *assumption* pending measurement on the built bar -- see `BLACK_KEY_GAIN`.

The palette itself is a taste call and is **not settled**: three candidates are provided
below and `PALETTE` selects between them.  Swap and look at the bar before deciding.
"""

from __future__ import annotations

from typing import Iterable

RGB = tuple[int, int, int]

# --------------------------------------------------------------------- the palettes

#: Warm->cool ramp, thumb warmest.  Maximum hue spacing between adjacent fingers, which
#: is what peripheral vision resolves best.  No blue channel dominance anywhere.
PALETTE_RAMP: dict[int, RGB] = {
    1: (255,  40,   0),   # thumb   - red-orange
    2: (255, 150,   0),   # index   - amber
    3: (210, 255,   0),   # middle  - yellow-green
    4: (  0, 255,  90),   # ring    - green
    5: (  0, 230, 200),   # little  - teal   (cyan-ish, still no pure blue)
}

#: Maximally distinct hues, ignoring any ordering metaphor.  Easier to tell apart when
#: two fingers light at once in a chord; harder to learn, because the order is arbitrary.
PALETTE_DISTINCT: dict[int, RGB] = {
    1: (255,  30,  30),   # thumb   - red
    2: (255, 170,   0),   # index   - orange
    3: (255, 255, 255),   # middle  - white   (anchor: the middle of the hand)
    4: (  0, 220,  60),   # ring    - green
    5: (255,   0, 200),   # little  - magenta, red-leaning so blue never dominates (D-13)
}

#: Thumb and little finger loud, inner fingers muted.  Bets that the player mainly needs
#: to know where the *ends* of the hand go and can infer 2-3-4 from the shape.
PALETTE_ANCHORS: dict[int, RGB] = {
    1: (255,   0,  30),   # thumb   - hot red
    2: (160,  90,  40),   # index   - dim orange
    3: ( 90, 130,  50),   # middle  - dim green
    4: ( 50, 130, 110),   # ring    - dim teal, green-leaning (blue must not dominate)
    5: (  0, 255, 170),   # little  - bright teal
}
# The inner three stay muted (that is this palette's premise) but are still held apart
# by the separation floor -- "muted" must not collapse into "indistinguishable".

PALETTES = {
    "ramp": PALETTE_RAMP,
    "distinct": PALETTE_DISTINCT,
    "anchors": PALETTE_ANCHORS,
}

#: Which one is live.  **Undecided** -- pick after looking at the built bar.
PALETTE = "ramp"

#: The hand pixel.  Deliberately low-saturation so it reads as "context" next to the
#: saturated finger hue rather than competing with it.
HAND_COLORS: dict[str, RGB] = {
    "L": (60, 60, 90),    # left  - cool grey
    "R": (90, 70, 40),    # right - warm grey
}

#: D-17.  **ASSUMED**, not measured: matte black keytops read roughly 40% dimmer than the
#: ivory-textured white ones, so their pixels are lifted.  Measure this on the real bar
#: and correct it here -- it is one number, in one place, on purpose.
BLACK_KEY_GAIN = 1.5

#: MIDI pitch classes that are black keys.
_BLACK_PCS = frozenset({1, 3, 6, 8, 10})


def is_black_key(midi: int) -> bool:
    return (midi % 12) in _BLACK_PCS


def _clamp8(v: float) -> int:
    return 0 if v < 0 else (255 if v > 255 else int(round(v)))


def scale(color: RGB, factor: float) -> RGB:
    """Scale a colour, clamping per channel.

    Note the deliberate consequence: a channel already at 255 cannot get brighter, so a
    gain applied to a saturated colour *shifts its hue* toward the unsaturated channels
    rather than brightening it.  `finger_color` therefore applies the black-key gain to
    the palette entry, and the palette entries are chosen with headroom in mind.
    """
    return (_clamp8(color[0] * factor), _clamp8(color[1] * factor), _clamp8(color[2] * factor))


def finger_color(finger: int, midi: int, brightness: float = 1.0,
                 palette: str | None = None) -> RGB:
    """Colour for the *finger* pixel of a key's pair."""
    if not 1 <= finger <= 5:
        raise ValueError("finger must be 1..5, got %r" % (finger,))
    table = PALETTES[palette or PALETTE]
    gain = BLACK_KEY_GAIN if is_black_key(midi) else 1.0
    return scale(table[finger], gain * brightness)


def hand_color(hand: str, midi: int, brightness: float = 1.0) -> RGB:
    """Colour for the *hand* pixel of a key's pair."""
    if hand not in HAND_COLORS:
        raise ValueError("hand must be 'L' or 'R', got %r" % (hand,))
    gain = BLACK_KEY_GAIN if is_black_key(midi) else 1.0
    return scale(HAND_COLORS[hand], gain * brightness)


def pair_colors(hand: str, finger: int, midi: int, brightness: float = 1.0,
                palette: str | None = None) -> tuple[RGB, RGB]:
    """Both pixels for one cued key, in (hand_pixel, finger_pixel) order.

    Pairs with `notemap.note_to_leds`, which returns the indices in the same order.
    """
    return (hand_color(hand, midi, brightness),
            finger_color(finger, midi, brightness, palette))


# ------------------------------------------------------------------ palette checking


def channel_distance(a: RGB, b: RGB) -> int:
    """Manhattan distance in RGB.

    Crude on purpose: a perceptual metric (CIEDE2000) would be more accurate but these
    are self-luminous emitters seen in peripheral vision through a diffuser, where the
    perceptual models do not hold well anyway.  Used only as a *floor* -- to catch a
    palette with two near-identical entries, not to rank good palettes.
    """
    return sum(abs(x - y) for x, y in zip(a, b))


def min_separation(palette: str | None = None) -> tuple[int, tuple[int, int]]:
    """Closest pair in a palette: (distance, (finger_a, finger_b))."""
    table = PALETTES[palette or PALETTE]
    worst, pair = 10**9, (0, 0)
    for i in range(1, 6):
        for j in range(i + 1, 6):
            d = channel_distance(table[i], table[j])
            if d < worst:
                worst, pair = d, (i, j)
    return worst, pair


def has_pure_blue(palette: str | None = None) -> list[int]:
    """Fingers whose colour is blue-dominant -- forbidden by D-13.

    'Blue-dominant' means the blue channel strictly exceeds both others; teal (equal or
    lower blue than green) is fine and is deliberately used for the little finger.
    """
    table = PALETTES[palette or PALETTE]
    return [f for f, (r, g, b) in table.items() if b > r and b > g]
