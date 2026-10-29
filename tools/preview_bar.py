"""Render what the LED bar would show, as a standalone HTML page.

Purpose: the palette choice (B2) is a taste call, and nobody can make it from RGB
tuples.  This draws the real 176-LED strip over a real keyboard, using the *actual*
firmware code paths -- `notemap.note_to_leds` and `colors.pair_colors` -- so what you see
is what the bar would light, not an illustration of it.

    python tools/preview_bar.py                  -> tools/preview_bar.html
    python tools/preview_bar.py --out other.html

It is a decision aid, not a simulator: a screen cannot reproduce a diffused emitter seen
in peripheral vision, and the black-key gain (D-17) is an unmeasured assumption.  Use it
to reject obviously-bad palettes, then decide properly on the built bar.
"""

from __future__ import annotations

import argparse
import html
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware", "src")
)

from fingerlight import colors  # noqa: E402
from fingerlight.lightscore import load  # noqa: E402
from fingerlight.notemap import StripSettings, note_to_leds  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXAMPLES = os.path.join(_ROOT, "lightscore", "examples")

WHITE_PCS = (0, 2, 4, 5, 7, 9, 11)
FINGER_NAMES = {1: "thumb", 2: "index", 3: "middle", 4: "ring", 5: "little"}


def is_white(midi: int) -> bool:
    return (midi % 12) in WHITE_PCS


def rgb(c) -> str:
    return "rgb(%d,%d,%d)" % c


def render_strip(pixels: dict[int, tuple[int, int, int]], s: StripSettings) -> str:
    """One row of `led_count` cells; lit pixels glow, the rest stay dark."""
    out = ['<div class="strip">']
    for i in range(s.led_count):
        c = pixels.get(i)
        if c is None:
            out.append('<i class="off"></i>')
        else:
            out.append(
                '<i style="background:%s;box-shadow:0 0 6px 2px %s"></i>' % (rgb(c), rgb(c))
            )
    out.append("</div>")
    return "".join(out)


def render_keys(cued: dict[int, tuple[str, int]], s: StripSettings) -> str:
    """A 52-white-key keyboard with the cued keys marked, so the strip has a referent."""
    out = ['<div class="keys">']
    for midi in range(21, 109):
        if not is_white(midi):
            continue
        cue = cued.get(midi)
        style = ""
        label = ""
        if cue:
            hand, finger = cue
            col = colors.finger_color(finger, midi)
            style = ' style="background:%s"' % rgb(col)
            label = "%s%d" % (hand, finger)
        out.append('<b class="w%s">%s</b>' % (style, html.escape(label)))
    out.append("</div>")
    return "".join(out)


def swatches(palette: str) -> str:
    rows = []
    for f in range(1, 6):
        white = colors.finger_color(f, 60, palette=palette)   # C4, a white key
        black = colors.finger_color(f, 61, palette=palette)   # C#4, gain applied
        rows.append(
            '<tr><td>%d <span class="nm">%s</span></td>'
            '<td><span class="sw" style="background:%s"></span> %s</td>'
            '<td><span class="sw" style="background:%s"></span> %s</td></tr>'
            % (f, FINGER_NAMES[f], rgb(white), rgb(white), rgb(black), rgb(black))
        )
    dist, pair = colors.min_separation(palette)
    return (
        '<table class="sw-table"><tr><th>finger</th><th>on a white key</th>'
        "<th>on a black key (gain %.2f)</th></tr>%s</table>"
        '<p class="meta">closest pair: fingers %s, distance %d &middot; '
        "blue-dominant entries: %s</p>"
        % (colors.BLACK_KEY_GAIN, "".join(rows), pair, dist,
           colors.has_pure_blue(palette) or "none")
    )


def build(score_path: str, s: StripSettings) -> str:
    score = load(score_path)
    title = score.meta.get("title", os.path.basename(score_path))

    blocks = []
    for palette in sorted(colors.PALETTES):
        frames = []
        for ev in score.events:
            pixels: dict[int, tuple[int, int, int]] = {}
            cued: dict[int, tuple[str, int]] = {}
            for n in ev.notes:
                lo, hi = note_to_leds(n.p, s)
                hand_px, finger_px = colors.pair_colors(
                    n.h, n.f, n.p, palette=palette
                )
                pixels[lo] = hand_px
                pixels[hi] = finger_px
                cued[n.p] = (n.h, n.f)
            label = ", ".join(
                "%s%d" % (n.h, n.f) for n in sorted(ev.notes, key=lambda n: n.p)
            )
            frames.append(
                '<div class="frame"><div class="t">t=%d ms &middot; %s</div>%s%s</div>'
                % (ev.t, html.escape(label), render_strip(pixels, s), render_keys(cued, s))
            )
        blocks.append(
            '<section><h2>%s%s</h2>%s<div class="frames">%s</div></section>'
            % (
                html.escape(palette),
                ' <span class="live">live</span>' if palette == colors.PALETTE else "",
                swatches(palette),
                "".join(frames),
            )
        )

    return """<!doctype html>
<meta charset="utf-8">
<title>LED bar preview</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;padding:24px;background:#0d0d10;color:#e6e6ea;
      font:14px/1.5 ui-sans-serif,system-ui,Segoe UI,sans-serif}
 h1{font-size:19px;margin:0 0 4px}
 h2{font-size:16px;margin:32px 0 8px;text-transform:lowercase;letter-spacing:.04em}
 .sub{color:#8b8b96;margin:0 0 8px;max-width:70ch}
 .live{font-size:11px;background:#2b6;color:#04120a;padding:1px 6px;border-radius:99px;
       vertical-align:middle;text-transform:uppercase;letter-spacing:.06em}
 section{border-top:1px solid #23232b;padding-top:4px}
 .strip{display:flex;gap:1px;background:#000;padding:4px;border-radius:3px;
        overflow-x:auto}
 .strip i{flex:1 0 3px;height:13px;border-radius:1px}
 .strip i.off{background:#191920}
 .keys{display:flex;gap:1px;margin-top:3px;overflow-x:auto}
 .keys b{flex:1 0 5px;height:30px;background:#f2f2ee;border-radius:0 0 2px 2px;
         color:#111;font:10px/30px ui-monospace,monospace;text-align:center;
         font-weight:600}
 .frame{margin:14px 0}
 .t{color:#8b8b96;font:11px ui-monospace,monospace;margin-bottom:3px}
 .sw-table{border-collapse:collapse;margin:6px 0;font-size:13px}
 .sw-table th{text-align:left;color:#8b8b96;font-weight:500;padding:2px 14px 2px 0}
 .sw-table td{padding:2px 14px 2px 0;font:12px ui-monospace,monospace}
 .nm{color:#8b8b96}
 .sw{display:inline-block;width:26px;height:12px;border-radius:2px;
     vertical-align:-1px;margin-right:4px}
 .meta{color:#8b8b96;font-size:12px;margin:2px 0 0}
 footer{margin-top:40px;color:#6a6a76;font-size:12px;max-width:70ch}
</style>
<h1>LED bar preview &mdash; %s</h1>
<p class="sub">Rendered through the real firmware path
(<code>notemap.note_to_leds</code> + <code>colors.pair_colors</code>), %d LEDs @ %d/m,
octave span %.1f&nbsp;mm. Left pixel of each pair = hand, right pixel = finger (D-12).</p>
%s
<footer>A screen cannot reproduce a diffused emitter in peripheral vision, and the
black-key gain (%.2f, D-17) is an unmeasured assumption. Use this to reject bad
palettes; decide on the built bar.</footer>
""" % (
        html.escape(title),
        s.led_count,
        s.leds_per_meter,
        s.octave_span_mm,
        "".join(blocks),
        colors.BLACK_KEY_GAIN,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "score",
        nargs="?",
        default=os.path.join(EXAMPLES, "two-hand-chords.lightscore.json"),
        help="a light-score JSON file (default: the two-hand chord fixture)",
    )
    ap.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview_bar.html"),
    )
    args = ap.parse_args()

    s = StripSettings.load()
    page = build(args.score, s)

    # Encode first, write to a temp path, then replace -- never truncate the target
    # before the content is known good.
    data = page.encode("utf-8")
    tmp = args.out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, args.out)
    print("wrote %s  (%d bytes, %d palettes)" % (args.out, len(data), len(colors.PALETTES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
