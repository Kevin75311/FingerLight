"""Verify the docs' links: every relative link resolves, every note reachable from index.

The notes link to each other with standard relative Markdown links, `[hardware](hardware.md)`,
because those work in both places the docs are read: on GitHub and in Obsidian.  Obsidian
`[[wikilinks]]` do not render on GitHub (they show as literal brackets), so any left in
the text is an error here.  `.obsidian/app.json` sets Obsidian to write relative
Markdown links, so new links come out in the right form.

Strips fenced blocks and inline code first -- neither renders as a link, so scanning raw
text over-reports (e.g. a `[[47,1],[79,1]]` settings value in a table).
"""
import collections
import os
import re
import sys
from urllib.parse import unquote

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKIP = (".obsidian", "scoreforge", "__pycache__", "external", ".pytest_cache", ".git")
# [text](target) and ![alt](target); the target ends at whitespace or the closing paren
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)", re.S)
WIKI = re.compile(r"\[\[[^\]]+\]\]")
FENCE = re.compile(r"^```.*?^```", re.M | re.S)
INLINE = re.compile(r"`[^`\n]*`")
EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)        # http:, https:, mailto:, ...


def strip_code(txt):
    return INLINE.sub("`x`", FENCE.sub("", txt))


notes = {}
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in SKIP]
    for f in fn:
        if f.endswith(".md"):
            p = os.path.join(dp, f)
            with open(p, encoding="utf-8") as fh:
                notes[os.path.relpath(p, ROOT).replace("\\", "/")] = fh.read()

broken, wikilinks, edges, total = [], [], collections.defaultdict(set), 0
for rel, txt in notes.items():
    body = strip_code(txt)
    for w in WIKI.findall(body):
        wikilinks.append((rel, w.replace("\n", " ")))
    for target in LINK.findall(body):
        if EXTERNAL.match(target) or target.startswith("#"):
            continue
        total += 1
        path = unquote(target.split("#", 1)[0])
        dest = os.path.normpath(os.path.join(os.path.dirname(rel), path)).replace("\\", "/")
        if not os.path.exists(os.path.join(ROOT, dest)):
            broken.append((rel, target))
        elif dest in notes and dest != rel:
            edges[rel].add(dest)

ENTRY = "docs/index.md"
seen, stack = {ENTRY}, [ENTRY]
while stack:
    cur = stack.pop()
    for nxt in edges.get(cur, ()):
        if nxt not in seen:
            seen.add(nxt)
            stack.append(nxt)

orphans = sorted(set(notes) - seen)
inbound = collections.Counter(d for dsts in edges.values() for d in dsts)
no_inbound = sorted(n for n in notes if inbound[n] == 0 and n != ENTRY)

print("notes scanned        : %d" % len(notes))
print("relative links       : %d" % total)
print("broken               : %d" % len(broken))
for s, t in broken:
    print("   BROKEN  %s -> %s" % (s, t))
print("wikilinks left       : %d" % len(wikilinks))
for s, t in wikilinks:
    print("   WIKI    %s: %s  (GitHub shows this as text; use [name](path.md))" % (s, t))
print("reachable from %s : %d / %d" % (ENTRY, len(seen), len(notes)))
print("unreachable          : %d" % len(orphans))
for o in orphans:
    print("   ORPHAN  %s" % o)
print("no inbound link      : %d" % len(no_inbound))
for n in no_inbound:
    print("   NO-BACKLINK  %s" % n)

sys.exit(1 if (broken or wikilinks or orphans) else 0)
