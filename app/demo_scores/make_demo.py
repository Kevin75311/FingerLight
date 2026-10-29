"""Regenerate the app's demo scores from the score compiler's own test fixtures.

    python app/demo_scores/make_demo.py

These are compiler output, not hand-written: never edit the .lightscore.json files,
re-run this.  They exist so a fresh Pi (or a PC preview) has something longer than the
two contract examples to practise on.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "score", "src"))

from fingerlight_score.musicxml import musicxml_to_lightscore  # noqa: E402

DEMOS = [
    ("grand-staff", "Grand-staff study (compiler fixture)"),
    ("hard", "Hard study (compiler fixture)"),
]


def main() -> None:
    for name, title in DEMOS:
        rel = "score/tests/fixtures/%s.musicxml" % name
        doc = musicxml_to_lightscore(os.path.join(REPO, *rel.split("/")),
                                     meta={"title": title, "source_file": rel})
        out = os.path.join(HERE, "%s.lightscore.json" % name)
        with open(out + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
        os.replace(out + ".tmp", out)
        print("%s: %d notes, tempo %s" % (out, len(doc["notes"]), doc.get("tempo")))


if __name__ == "__main__":
    main()
