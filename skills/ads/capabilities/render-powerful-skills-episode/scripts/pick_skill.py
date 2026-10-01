#!/usr/bin/env python3
"""Pick the next episode's skill. No human.

    python scripts/pick_skill.py --series projects/<series>                  # reads the live site
    python scripts/pick_skill.py --series projects/<series> --from menu.json # a saved menu

Choosing the skill was one of the three things an episode needed from a person. Once two
facts are known it is a rule, not a judgement:

  1. ONLY skills in the site's hero-demo menu can be featured. The screen recording plays
     that skill's real transcript, and a skill outside the menu dies in State 2 with "no
     skill matching ...". The site has 120-odd skill pages but, measured, 35 runnable demos.
     list-demo-skills.js reads that menu the same way the recorder does.
  2. The series should ROTATE categories. So: the category with the fewest episodes so far,
     never the same category as the previous episode, then menu order; and inside it the
     first skill not yet used.

Used skills are matched by slug, through defaults/skill-aliases.json and any episode's
screen_skill, exactly as make_screen_recording.py resolves them -- so an episode featuring
"goose-graphics" counts as having used "Make a LinkedIn carousel".
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def slug(t):
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", str(t).lower()))


def load_menu(src):
    if src:
        return json.loads(Path(src).read_text(encoding="utf-8"))
    r = subprocess.run(["node", str(HERE / "list-demo-skills.js")], capture_output=True, text=True)
    if r.returncode:
        sys.exit("could not read the live demo menu:\n" + r.stderr[-800:])
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    return json.loads(r.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True, help="folder holding episode-*/episode.json")
    ap.add_argument("--from", dest="menu", help="a saved list-demo-skills.js output")
    ap.add_argument("--json", action="store_true", help="print only the pick, as JSON")
    a = ap.parse_args()

    menu = load_menu(a.menu)
    cats = menu["categories"]
    aliases = json.loads((ROOT / "defaults" / "skill-aliases.json").read_text(encoding="utf-8"))
    cat_of = {x["slug"]: c for c, ls in cats.items() for x in ls}

    eps = []
    for f in Path(a.series).glob("episode-*/episode.json"):
        n = int(re.search(r"episode-(\d+)", str(f)).group(1))
        e = json.loads(f.read_text(encoding="utf-8"))
        s = e.get("screen_skill") or aliases.get(e.get("featured_skill"), e.get("featured_skill"))
        eps.append((n, slug(s) if s else None, e.get("part_number")))
    eps.sort()
    used = {s for _, s, _ in eps if s}
    stray = sorted(s for s in used if s not in cat_of)
    if stray:
        print("[pick] NOTE: used skills not in today's menu (renamed or removed on the site): %s"
              % ", ".join(stray), file=sys.stderr)

    uses = {c: sum(1 for _, s, _ in eps if cat_of.get(s) == c) for c in cats}
    last = cat_of.get(eps[-1][1]) if eps else None
    order = list(cats)
    ranked = []
    for c in order:
        free = [x for x in cats[c] if x["slug"] not in used]
        if free:
            ranked.append((uses[c], c == last, order.index(c), c, free[0], len(free)))
    if not ranked:
        sys.exit("[pick] every demo-able skill has been used")
    ranked.sort()
    _, _, _, cat, pick, left = ranked[0]
    # The part number is the NEXT PART, not the next folder. They are not the same on a
    # series whose folder numbering predates it: @gooseaitools has episode-1 as an older
    # build, so parts 1 and 2 live in episode-2 and episode-3 and a folder count returns
    # 3 for what is really part 3 only by accident. Read the parts the episodes declare,
    # and fall back to folder numbers only if none of them declare one.
    parts = [p_ for _, _, p_ in eps if isinstance(p_, int)]
    nxt = (max(parts) + 1) if parts else ((eps[-1][0] + 1) if eps else 1)
    result = {"part_number": nxt, "featured_skill": pick["slug"], "site_label": pick["label"],
              "category": cat}

    if a.json:
        print(json.dumps(result))
        return
    print("[pick] %d episodes so far; last category: %s" % (len(eps), last))
    print("[pick] episodes per category: %s" % ", ".join("%s %d" % (c, uses[c]) for c in order))
    print("[pick] unused demo-able skills: %d of %d"
          % (sum(1 for ls in cats.values() for x in ls if x["slug"] not in used),
             sum(len(ls) for ls in cats.values())))
    print("\nPICK for episode %d: %s  (%s, %d left in that category)" % (nxt, pick["label"], cat, left))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
