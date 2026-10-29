"""Compile sheet music into a light-score the Pi can play.  Runs on the PC (D-23).

    python tools/compile_score.py piece.musicxml            # -> piece.lightscore.json
    python tools/compile_score.py piece.mxl --title "Satie, Gymnopedie 1"
    python tools/compile_score.py scan.pdf                  # via Audiveris (UNVERIFIED)

Then copy the .lightscore.json into ~/.fingerlight/scores on the Pi and press Rescan in
the Library.  The output is validated with the firmware's own reader before it is
written, so a file this tool produces is one the Pi will load.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "score", "src"))
sys.path.insert(0, os.path.join(REPO, "firmware", "src"))

from fingerlight.lightscore import parse  # noqa: E402
from fingerlight_score.musicxml import musicxml_to_lightscore  # noqa: E402

OMR_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MusicXML / .mxl (or PDF via Audiveris) -> light-score")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", help="default: <input stem>.lightscore.json beside it")
    ap.add_argument("--title", help="shown in the app's library (default: the score's own title)")
    ap.add_argument("--hands", choices=("auto", "staff", "pitch"), default="auto",
                    help="how notes are split between hands (see score/src/fingerlight_score/hands.py)")
    args = ap.parse_args(argv)

    src = args.input
    low = src.lower()
    meta = {"title": args.title} if args.title else {}
    if low.endswith(OMR_SUFFIXES):
        from fingerlight_score.omr import omr_to_lightscore
        doc = omr_to_lightscore(src, hand_mode="staff" if args.hands == "auto" else args.hands,
                                meta=meta or None)
    else:
        doc = musicxml_to_lightscore(src, hand_mode=args.hands, meta=meta or None)
    if not doc["meta"].get("title"):
        doc["meta"]["title"] = os.path.splitext(os.path.basename(src))[0]

    score = parse(doc)                      # the Pi's own reader; raises if unplayable
    stem = os.path.basename(src)
    for suf in (".musicxml", ".mxl", ".xml") + OMR_SUFFIXES:
        if stem.lower().endswith(suf):
            stem = stem[: -len(suf)]
            break
    out = args.output or os.path.join(os.path.dirname(os.path.abspath(src)),
                                      stem + ".lightscore.json")
    with open(out + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    os.replace(out + ".tmp", out)
    hands = sorted({n.h for n in score.notes})
    print("%s\n  %r: %d notes, %d events, hands %s, %.1f s"
          % (out, doc["meta"]["title"], len(score.notes), len(score.events),
             "+".join(hands), score.duration_ms / 1000))
    return 0


if __name__ == "__main__":
    sys.exit(main())
