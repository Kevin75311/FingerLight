"""Verify the Obsidian vault: every [[wikilink]] resolves, every note reachable from index.

Strips fenced blocks and inline code first -- Obsidian does not linkify inside either,
so scanning raw text over-reports (e.g. a `[[47,1],[79,1]]` settings value in a table).
Resolves both name-style ([[index]]) and path-style ([[firmware/README]]) links.
"""
import os, re, sys, collections

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LINK = re.compile(r"\[\[([^\]\|#]+?)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
FENCE = re.compile(r"^```.*?^```", re.M | re.S)
INLINE = re.compile(r"`[^`\n]*`")


def strip_code(txt):
    return INLINE.sub("`x`", FENCE.sub("", txt))


notes = {}
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in (".obsidian", "scoreforge", "__pycache__", "external", ".pytest_cache", ".git")]
    for f in fn:
        if f.endswith(".md"):
            p = os.path.join(dp, f)
            with open(p, encoding="utf-8") as fh:
                notes[os.path.relpath(p, ROOT).replace("\\", "/")] = fh.read()

# Obsidian's resolution table: basename, frontmatter name, aliases, and full path
resolve = collections.defaultdict(set)
for rel, txt in notes.items():
    resolve[rel[:-3].lower()].add(rel)                      # path-style
    resolve[os.path.basename(rel)[:-3].lower()].add(rel)    # basename
    fm = txt.split("---")[1] if txt.startswith("---") else ""
    m = re.search(r"^name:\s*(.+)$", fm, re.M)
    if m:
        resolve[m.group(1).strip().lower()].add(rel)
    m = re.search(r"^aliases:\s*\[(.+?)\]", fm, re.M)
    if m:
        for a in m.group(1).split(","):
            resolve[a.strip().strip("\"'").lower()].add(rel)

by_path = {rel.lower(): rel for rel in notes}
broken, ambiguous, edges, total = [], [], collections.defaultdict(set), 0
for rel, txt in notes.items():
    for target in LINK.findall(strip_code(txt)):
        total += 1
        key = target.strip().lower()
        # Obsidian resolves an exact vault-path match before any basename match
        exact = key + '.md'
        hits = {by_path[exact]} if exact in by_path else resolve.get(key)
        if not hits:
            broken.append((rel, target.strip()))
        else:
            if len(hits) > 1:
                ambiguous.append((rel, target.strip(), sorted(hits)))
            edges[rel] |= hits

ENTRY = "docs/index.md"
seen, stack = {ENTRY}, [ENTRY]
while stack:
    cur = stack.pop()
    for nxt in edges.get(cur, ()):
        if nxt not in seen:
            seen.add(nxt)
            stack.append(nxt)

orphans = sorted(set(notes) - seen)
inbound = collections.Counter()
for src, dsts in edges.items():
    for d in dsts:
        if d != src:
            inbound[d] += 1
no_inbound = sorted(n for n in notes if inbound[n] == 0 and n != ENTRY)

print("notes scanned        : %d" % len(notes))
print("wikilinks (code-free): %d" % total)
print("broken               : %d" % len(broken))
for s, t in broken:
    print("   BROKEN  %s -> [[%s]]" % (s, t))
print("ambiguous            : %d" % len(ambiguous))
for s, t, h in ambiguous:
    print("   AMBIG   %s -> [[%s]] matches %s" % (s, t, h))
print("reachable from %s : %d / %d" % (ENTRY, len(seen), len(notes)))
print("unreachable          : %d" % len(orphans))
for o in orphans:
    print("   ORPHAN  %s" % o)
print("no inbound link      : %d" % len(no_inbound))
for n in no_inbound:
    print("   NO-BACKLINK  %s" % n)

sys.exit(1 if (broken or orphans or ambiguous) else 0)
