"""Staged bring-up for the LED bar. Run the stages in order; stop at the first failure.

    python tools/hw/led_test.py stages          # what each stage proves
    python tools/hw/led_test.py first           # LED 0 only, dim red
    python tools/hw/led_test.py walk --ms 40    # one lit LED runs the length
    python tools/hw/led_test.py count           # light 0, the middle, and the last
    python tools/hw/led_test.py order           # R then G then B, whole strip
    python tools/hw/led_test.py current --pct 25
    python tools/hw/led_test.py map C4 E4 G4    # notes, through the real firmware map
    python tools/hw/led_test.py sweep           # every note A0..C8, in order

    python tools/hw/led_test.py fx              # light show: every effect, 10 s each
    python tools/hw/led_test.py fx fire --secs 60   # rainbow comet fire twinkle ripple keys

Every stage prints what you should SEE. If what you see differs, that is the
finding -- the strip is telling you which assumption is wrong.

Defaults come from `data/led_map.json` (176 LEDs), so the count is never retyped.
Brightness defaults to 15% because a bare 176-LED strip at full white pulls ~10 A
and this is a bench test, not a light show.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "firmware", "src"))

STAGES = [
    ("first", "SPI reaches the strip at all, and the data line survives the level shifter"),
    ("walk", "every LED works, in the order you think, with no dead or skipped pixel"),
    ("count", "the strip really has as many LEDs as the map assumes"),
    ("order", "the colour order is GRB (if red shows green, pass --order RGB)"),
    ("current", "the supply holds up: measure amps here, not later"),
    ("map", "the firmware's note->LED map lands on the keys you expect"),
    ("sweep", "the whole keyboard, end to end, at the pitch the design assumes"),
]


def led_count(default=176):
    try:
        with open(os.path.join(ROOT, "data", "led_map.json"), "rb") as f:
            return int(json.load(f)["led_count"])
    except Exception:
        return default


def saved_mirror():
    """The app's `led_mirror` setting, so this tool wires the bar the same way."""
    try:
        path = os.path.join(os.path.expanduser("~"), ".fingerlight", "settings.json")
        with open(path, encoding="utf-8") as f:
            return bool(json.load(f).get("led_mirror", False))
    except Exception:
        return False


def make_strip(args):
    from ws2812_spi import Strip

    s = Strip(args.n, order=args.order, hz=args.hz, brightness=args.brightness,
              mirror=args.mirror)
    if args.one_pattern:
        from ws2812_spi import _table
        s._table = _table(0b1000, int(args.one_pattern, 2))
    return s


def say(msg):
    print("  LOOK FOR: " + msg, flush=True)


# ------------------------------------------------------------------- stages
def stage_first(s, args):
    say("LED 0 -- the one nearest the data-in end -- dim red, nothing else lit.")
    s.clear()
    s[0] = (60, 0, 0)
    s.show()
    time.sleep(args.hold)


def stage_walk(s, args):
    say("a single dim white pixel travelling from the data-in end to the far end, "
        "smoothly, with no gaps and no flicker.")
    for i in range(s.n):
        s.clear()
        s[i] = (40, 40, 40)
        s.show()
        time.sleep(0 if args.dry_run else args.ms / 1000.0)
    s.clear()
    s.show()


def stage_count(s, args):
    last = s.n - 1
    mid = s.n // 2
    say("exactly three lit pixels: RED at LED 0, GREEN at LED %d (the middle), "
        "BLUE at LED %d (the far end)." % (mid, last))
    print("  If blue is NOT the last physical LED, the strip is longer or shorter than %d."
          % s.n)
    s.clear()
    s[0] = (80, 0, 0)
    s[mid] = (0, 80, 0)
    s[last] = (0, 0, 80)
    s.show()
    time.sleep(0 if args.dry_run else args.hold)


def stage_order(s, args):
    for name, rgb in (("RED", (60, 0, 0)), ("GREEN", (0, 60, 0)), ("BLUE", (0, 0, 60))):
        say("the whole strip %s." % name)
        s.fill(rgb)
        s.show()
        time.sleep(0 if args.dry_run else args.hold)
    s.clear()
    s.show()


def stage_current(s, args):
    pct = args.pct / 100.0
    v = int(255 * pct)
    say("the whole strip white at %d%%. Put a meter on the 5 V feed now." % args.pct)
    print("  Expect roughly %.1f A (176 LEDs x 60 mA x %.2f). If the far end is dimmer or\n"
          "  pink, that is voltage drop -- inject power at the far end too."
          % (s.n * 0.06 * pct, pct))
    s.fill((v, v, v))
    s.show()
    time.sleep(0 if args.dry_run else args.hold)
    s.clear()
    s.show()


def _note_leds(notes):
    from fingerlight import colors, notemap

    settings = notemap.StripSettings.load()
    out = []
    for i, name in enumerate(notes):
        midi = _midi(name)
        lo, hi = notemap.note_to_leds(midi, settings)
        hand, finger = ("R", (i % 5) + 1)
        hand_rgb, finger_rgb = colors.pair_colors(hand, finger, midi)
        out.append((name, midi, lo, hi, hand_rgb, finger_rgb))
    return out


_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _midi(text):
    import re

    m = re.match(r"^([A-Ga-g])([#b]?)(-?\d)$", text.strip())
    if not m:
        raise SystemExit("bad note %r (want C4, F#3, Bb2)" % text)
    semi = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1).upper()]
    acc = 1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0
    return (int(m.group(3)) + 1) * 12 + semi + acc


def stage_map(s, args):
    if not args.notes:
        args.notes = ["A0", "C4", "C8"]
    rows = _note_leds(args.notes)
    say("one lit pair per note, each sitting over its own key:")
    for name, midi, lo, hi, hrgb, frgb in rows:
        print("     %-4s midi %3d -> LED %3d (hand %s) + LED %3d (finger %s)"
              % (name, midi, lo, tuple(hrgb), hi, tuple(frgb)))
    s.clear()
    for _name, _midi_, lo, hi, hrgb, frgb in rows:
        s[lo] = hrgb
        s[hi] = frgb
    s.show()
    time.sleep(0 if args.dry_run else args.hold)


def stage_sweep(s, args):
    from fingerlight import colors, notemap

    settings = notemap.StripSettings.load()
    say("the cue walking up the keyboard, one key at a time, A0 to C8. Hold a ruler "
        "against the keys: the lit pair should track the key centres, not drift.")
    for k, midi in enumerate(range(21, 109)):
        lo, hi = notemap.note_to_leds(midi, settings)
        hand_rgb, finger_rgb = colors.pair_colors("R", (k % 5) + 1, midi)
        s.clear()
        s[lo] = hand_rgb
        s[hi] = finger_rgb
        s.show()
        time.sleep(0 if args.dry_run else args.ms / 1000.0)
    s.clear()
    s.show()


STAGE_FN = {"first": stage_first, "walk": stage_walk, "count": stage_count,
            "order": stage_order, "current": stage_current, "map": stage_map,
            "sweep": stage_sweep}


# ------------------------------------------------------------------ effects
# Not bring-up stages: these run once the strip has passed them, to show it off and to
# soak-test it (a minute of `fire` exercises every LED at every level). All are
# time-based, so they look the same at any --fps.

def _hsv(h, sat=1.0, val=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, sat, val)
    return (int(r * 255), int(g * 255), int(b * 255))


def _animate(s, args, render):
    """Call render(t, dt) once per frame for --secs, paced to --fps."""
    period = 1.0 / args.fps
    start = time.perf_counter()
    for k in range(int(args.secs * args.fps)):
        render(k * period, period)
        s.show()
        if not args.dry_run:
            delay = start + (k + 1) * period - time.perf_counter()
            if delay > 0:
                time.sleep(delay)


def fx_rainbow(s, args):
    say("the full colour wheel stretched along the strip, scrolling smoothly.")
    n = s.n

    def render(t, dt):
        s.buf = [_hsv(i / n - t * 0.25) for i in range(n)]

    _animate(s, args, render)


def fx_comet(s, args):
    say("a bright comet bouncing end to end, changing colour, trailing a fading tail.")
    n = s.n
    glow = [[0.0, 0.0, 0.0] for _ in range(n)]

    def render(t, dt):
        fade = 0.5 ** (dt / 0.06)           # tail halves every 60 ms
        for px in glow:
            px[0] *= fade
            px[1] *= fade
            px[2] *= fade
        phase = (t / 1.6) % 2.0             # 1.6 s per crossing
        pos = (phase if phase < 1.0 else 2.0 - phase) * (n - 1)
        head = _hsv(t * 0.1)
        i = int(pos)
        for j, w in ((i, 1.0 - (pos - i)), (i + 1, pos - i)):   # anti-aliased head
            if 0 <= j < n:
                for c in range(3):
                    glow[j][c] = max(glow[j][c], head[c] * w)
        s.buf = [(int(r), int(g), int(b)) for r, g, b in glow]

    _animate(s, args, render)


def fx_fire(s, args):
    say("flames licking in from both ends towards the middle: red, orange, yellow tips.")
    half = (s.n + 1) // 2
    heat = [0.0] * half

    def colour(h):                          # black -> red -> yellow -> white
        h = min(1.0, h)
        if h < 0.4:
            return (int(255 * h / 0.4), 0, 0)
        if h < 0.8:
            return (255, int(255 * (h - 0.4) / 0.4), 0)
        return (255, 255, int(255 * (h - 0.8) / 0.2))

    def render(t, dt):
        k = dt * 60.0                       # tuned at 60 fps; scale for others
        for i in range(half):               # cool every cell a little
            heat[i] = max(0.0, heat[i] - random.random() * 0.22 * k * 10.0 / half)
        for i in range(half - 1, 1, -1):    # heat drifts away from the base
            heat[i] = (heat[i - 1] + 2 * heat[i - 2]) / 3.0
        if random.random() < 0.5 * k:       # new sparks near the base
            j = random.randint(0, min(6, half - 1))
            heat[j] = min(1.0, heat[j] + 0.6 + random.random() * 0.4)
        row = [colour(h) for h in heat]
        s.buf = (row + row[::-1])[:s.n] if s.n % 2 == 0 else row + row[-2::-1]

    _animate(s, args, render)


def fx_twinkle(s, args):
    say("a deep-blue night sky with stars fading in and out at random.")
    n = s.n
    stars = []                              # [index, born, life, rgb]

    def render(t, dt):
        while random.random() < 6.0 * dt * n / 176:          # ~6 new stars a second
            stars.append([random.randrange(n), t, 0.6 + random.random() * 1.4,
                          random.choice([(255, 220, 160), (200, 220, 255), (255, 255, 255)])])
        stars[:] = [st for st in stars if t - st[1] < st[2]]
        buf = [(0, 0, 10)] * n
        for i, born, life, rgb in stars:
            a = math.sin(math.pi * (t - born) / life)       # 0 -> 1 -> 0 over its life
            buf[i] = tuple(max(int(c * a), b) for c, b in zip(rgb, buf[i]))
        s.buf = buf

    _animate(s, args, render)


def fx_ripple(s, args):
    say("rain on a pond: drops land at random and rings of colour spread outwards.")
    n = s.n
    drops = []                              # [centre, born, hue]

    def render(t, dt):
        if random.random() < dt / 0.4:      # one drop every ~0.4 s
            drops.append([random.uniform(0, n - 1), t, random.random()])
        drops[:] = [d for d in drops if t - d[1] < 2.5]
        acc = [[0.0, 0.0, 0.0] for _ in range(n)]
        for centre, born, hue in drops:
            age = t - born
            radius = age * 40.0             # LEDs per second
            amp = math.exp(-age * 1.6)
            rgb = _hsv(hue)
            lo, hi = int(centre - radius - 4), int(centre + radius + 4)
            for i in range(max(0, lo), min(n, hi + 1)):
                ring = abs(abs(i - centre) - radius)
                w = amp * math.exp(-ring * ring / 4.0)
                for c in range(3):
                    acc[i][c] += rgb[c] * w
        s.buf = [tuple(min(255, int(v)) for v in px) for px in acc]

    _animate(s, args, render)


def fx_keys(s, args):
    say("a ghost pianist: C-G-Am-F arpeggios rolling up and down the keyboard, each key "
        "lighting its own LED pair (hand colour + finger colour) and fading out.")
    from fingerlight import colors, notemap

    settings = notemap.StripSettings.load()
    chords = [(0, 4, 7), (7, 11, 14), (9, 12, 16), (5, 9, 12)]   # C G Am F, from C
    notes = []
    for root in chords:
        up = [36 + 12 * octv + iv for octv in range(4) for iv in root]
        notes += up + up[-2:0:-1]
    step = 0.09                             # seconds per note
    lit = {}                                # led -> (rgb, struck_at)
    struck = [0]                            # notes played so far

    def render(t, dt):
        while struck[0] * step <= t:
            k = struck[0]
            midi = notes[k % len(notes)]
            lo, hi = notemap.note_to_leds(midi, settings)
            hrgb, frgb = colors.pair_colors("L" if midi < 60 else "R", k % 5 + 1, midi)
            lit[lo], lit[hi] = (hrgb, t), (frgb, t)
            struck[0] += 1
        buf = [(0, 0, 0)] * s.n
        for led, (rgb, at) in lit.items():
            a = 0.5 ** ((t - at) / 0.25)    # each note decays like a struck string
            if a > 0.02:
                buf[led] = tuple(int(c * a) for c in rgb)
        s.buf = buf

    _animate(s, args, render)


FX_FN = {"rainbow": fx_rainbow, "comet": fx_comet, "fire": fx_fire,
         "twinkle": fx_twinkle, "ripple": fx_ripple, "keys": fx_keys}


def stage_fx(s, args):
    names = args.notes or list(FX_FN)
    bad = [x for x in names if x not in FX_FN]
    if bad:
        raise SystemExit("unknown effect %s (have: %s)" % (", ".join(bad), " ".join(FX_FN)))
    try:
        for name in names:
            print("fx %s -- %.0f s" % (name, args.secs), flush=True)
            FX_FN[name](s, args)
    except KeyboardInterrupt:
        print("  stopped.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["stages"] + list(STAGE_FN) + ["fx"])
    ap.add_argument("notes", nargs="*",
                    help="`map`: notes, e.g. C4 E4 G4; `fx`: effects, e.g. fire comet "
                         "(default: all of %s)" % ", ".join(FX_FN))
    ap.add_argument("-n", type=int, default=led_count(), help="LEDs on the strip")
    ap.add_argument("--order", default="GRB", help="wire colour order (WS2812B is GRB)")
    ap.add_argument("--hz", type=int, default=3_200_000)
    ap.add_argument("--brightness", type=float, default=0.15)
    ap.add_argument("--ms", type=int, default=25, help="per step, walk/sweep")
    ap.add_argument("--hold", type=float, default=3.0, help="seconds to hold a static test")
    ap.add_argument("--pct", type=int, default=25, help="white level for the current stage")
    ap.add_argument("--secs", type=float, default=10.0, help="seconds per effect, fx")
    ap.add_argument("--fps", type=int, default=60, help="frames per second, fx")
    ap.add_argument("--dry-run", action="store_true",
                    help="no SPI: print what would be sent (works on any machine)")
    ap.add_argument("--one-pattern", default="", help="e.g. 1100 to shorten T1H if a strip is fussy")
    ap.add_argument("--mirror", action=argparse.BooleanOptionalAction, default=None,
                    help="data input at the C8 end (default: the app's led_mirror setting)")
    args = ap.parse_args()
    if args.mirror is None:
        args.mirror = saved_mirror()

    if args.stage == "stages":
        print("Run these in order. Each one proves something the next one assumes.\n")
        for name, proves in STAGES:
            print("  %-8s %s" % (name, proves))
        print("\nDefault strip length: %d LEDs (from data/led_map.json)" % args.n)
        return 0

    print("stage %s -- %d LEDs, %s%s, %.0f%% brightness, %.1f MHz"
          % (args.stage, args.n, args.order, ", mirrored" if args.mirror else "",
             args.brightness * 100, args.hz / 1e6))
    strip = make_strip(args)
    try:
        strip.open(dry=args.dry_run)
    except ImportError as e:
        raise SystemExit(
            "cannot import the SPI driver (%s).\n"
            "  On the Pi:  sudo apt install python3-spidev, and enable SPI\n"
            "              (tools/hw/setup_pi.sh does both)\n"
            "  Elsewhere:  add --dry-run to rehearse the stage and read the numbers\n"
            "              it would send." % e)
    try:
        (stage_fx if args.stage == "fx" else STAGE_FN[args.stage])(strip, args)
    finally:
        strip.clear()
        strip.show()
        counter = strip.spi          # read it BEFORE close(), which drops the handle
        strip.close()
    if args.dry_run:
        print("dry run: %d frames, %d bytes -- nothing left the machine"
              % (counter.frames, counter.bytes))
    print("stage %s done." % args.stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
