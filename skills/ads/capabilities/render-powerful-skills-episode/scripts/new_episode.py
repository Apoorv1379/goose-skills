#!/usr/bin/env python3
"""Start the next episode from nothing: pick the skill, read its demo, write the lines. No human.

    python scripts/new_episode.py --series projects/<series>            # pick + write + free steps
    python scripts/new_episode.py --series projects/<series> --skill "Find warm intros"
    python scripts/new_episode.py --series projects/<series> --no-build   # stop at episode.json

This is the chain the SKILL.md used to hand a person between steps:

  pick_skill.py        the least-used category's next unused demo on the live site
  skill-transcript.js  plays that demo and returns the command and the result it ends on
  write_lines.py       writes the four lines from that run, gated by the shipped episodes
  make_episode.py      every FREE step: the screen recording, the plan, the cost

It never passes --yes. The paid wave (~$7.20 of Veo) is the one click a person still makes,
after looking at the lines and the screen recording this produced -- the repo's lock order.
"""
import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def run(cmd, capture=False):
    print("\n$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], capture_output=capture, text=True, encoding="utf-8")
    if r.returncode:
        if capture:
            print(r.stderr[-1500:], file=sys.stderr)
        sys.exit("[new] step failed: %s" % pathlib.Path(str(cmd[1])).name)
    return r.stdout



def scaffold_payoff(ep, tr):
    """Draft the payoff spec from the demo, so nobody starts it from a blank page.

    Which treatment is a judgement call and stays one -- this only guesses from what the
    run WROTE, because that is what the payoff shows: a file of rows gets the sheet, a
    written report gets the document. Both @gooseaitools episodes had this written by
    hand, which is a step that silently does not happen when someone is in a hurry.

    The figures are lifted out of the demo's own closing lines. They still have to be
    checked against the transcript and the still still has to be approved before the
    episode is built -- that gate is the point, not an obstacle.
    """
    import json
    import re

    t = json.loads(tr.read_text(encoding="utf-8"))
    chip = (t.get("deliverable_chip") or "").strip()
    tail = " ".join(t.get("last_lines") or []) or (t.get("result") or "")
    body = " ".join(t.get("transcript") or []) + " " + tail
    rows_file = chip.lower().endswith((".csv", ".tsv"))
    # A .pdf of SLIDES and a .pdf REPORT want opposite treatments and the extension
    # cannot tell them apart: part 4 writes exports/...-carousel.pdf and the extension
    # said "report" when the run had actually rendered eight slides. The transcript can
    # tell them apart, so read that instead.
    made_images = any(w in body.lower() for w in ("slide", "png", "image", "thumbnail", "render"))
    work = ep / "working"
    work.mkdir(parents=True, exist_ok=True)
    kind = "sheet" if rows_file else "board" if made_images else "report"
    out = work / ("%s-spec.json" % kind)
    if out.exists():
        return out

    # Deliberately NOT pre-filling the figures. A regex over the closing lines pulled
    # "30" out of "Last 30 days" and "2026" out of a filename date -- a wrong number sitting
    # in a field invites someone to ship it, which is worse than an empty field. The run's
    # own lines go in under $from_the_run instead, to be read and typed.
    figs = []

    if kind == "board":
        spec = {
            "kicker": "REPLACE: the frame, e.g. PAST 14 DAYS",
            "headline": "REPLACE|REPLACE",
            "rows": [{"n": "REPLACE", "who": "REPLACE", "themes": "REPLACE"}],
            "focus_row": 0,
            "callout": {"label": "REPLACE", "value": "REPLACE", "sub": "REPLACE"},
            "$why_board": ("This run RENDERED something -- slides, images, PNGs. If the "
                           "real stills exist, show THOSE full frame with make-broll.py: "
                           "finished creative is the strongest payoff there is. A board "
                           "is the fallback for when the demo reports what it made "
                           "without handing the files over, which is the usual case."),
            "$from_the_run": t.get("last_lines") or [t.get("result", "")],
            "$check": ("Every value from the run. Then make-board-broll.py --spec this "
                       "--still review/broll-still.png --still-only, look at it, and "
                       "only then build."),
        }
    elif rows_file:
        spec = {
            "file": chip or "output.csv",
            "filters": "REPLACE: the search the run actually ran",
            "count_before": "REPLACE: what went in, e.g. 412 scored",
            "count_after": "REPLACE: what came out, e.g. 127 hot",
            "columns": ["COLUMN", "COLUMN", "COLUMN"],
            "score_columns": [1, 2],
            "rows": [{"cells": ["REPLACE", "REPLACE", "REPLACE"], "hot": True}],
            "$from_the_run": t.get("last_lines") or [t.get("result", "")],
            "$check": ("Every cell must come from the run. No invented names. Then: "
                       "make-sheet-broll.py --spec this --still review/broll-still.png "
                       "--still-only, look at it, and only then build."),
        }
    else:
        spec = {
            "file": chip or "report.md",
            "title": "REPLACE: what the report is",
            "meta": "REPLACE: the account, window and scope the run used",
            "figures": [{"label": "REPLACE", "value": "REPLACE"},
                        {"label": "REPLACE", "value": "REPLACE"}],
            "table": {"title": "REPLACE", "columns": ["A", "B"], "rows": []},
            "$from_the_run": t.get("last_lines") or [t.get("result", "")],
            "$check": ("Every figure must come from the run. Then: make-report-broll.py "
                       "--spec this --still review/broll-still.png --still-only, look at "
                       "it, and only then build."),
        }
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + chr(10), encoding="utf-8")
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True, type=pathlib.Path)
    ap.add_argument("--skill", help="skip the pick and use this site label or slug")
    ap.add_argument("--no-build", action="store_true", help="stop once episode.json is written")
    a = ap.parse_args()

    py = sys.executable
    if a.skill:
        label = a.skill
        eps = sorted(int(p.name.split("-")[1]) for p in a.series.glob("episode-*")
                     if p.name.split("-")[1].isdigit())
        part = (eps[-1] + 1) if eps else 1
    else:
        pick = json.loads(run([py, HERE / "pick_skill.py", "--series", a.series, "--json"], True))
        label, part = pick["site_label"], pick["part_number"]
        print("[new] episode %d: %s (%s)" % (part, label, pick["category"]))

    # The folder is the next FREE folder, which is not the part number: this series has
    # an episode-1 that predates the format, so part 1 lives in episode-2. Naming the
    # folder after the part collides with an existing one and the run dies before it
    # starts. part_number is what the caption renders; the folder is just storage.
    n = part
    while (a.series / ("episode-%d" % n)).exists():
        n += 1
    ep = a.series / ("episode-%d" % n)
    if n != part:
        print("[new] part %d goes in %s (episode-%d is taken)" % (part, ep.name, part))
    spec = ep / "episode.json"
    if spec.exists():
        sys.exit("[new] %s already exists; build it with make_episode.py or delete it first" % spec)
    ep.mkdir(parents=True, exist_ok=True)
    tr = ep / "demo-transcript.json"
    tr.write_text(run(["node", HERE / "skill-transcript.js", label], True), encoding="utf-8")

    run([py, HERE / "write_lines.py", "--series", a.series, "--transcript", tr,
         "--part", part, "--out", spec])

    spec_file = scaffold_payoff(ep, tr)
    print()
    print("[new] payoff draft: %s" % spec_file)
    print("      Fill it from the run, render the still, LOOK at it, then build:")
    print("        make-%s-broll.py --spec %s --still %s --still-only --out x.mp4"
          % ("sheet" if spec_file.name.startswith("sheet") else "report",
             spec_file, ep / "review" / "broll-still.png"))

    if a.no_build:
        print("\n[new] wrote %s" % spec)
        return
    run([py, HERE / "make_episode.py", "--episode", ep])
    print("\n[new] free steps done. Read the lines in %s and watch the screen recording, then:"
          "\n      python %s --episode %s --yes" % (spec, HERE / "make_episode.py", ep))


if __name__ == "__main__":
    main()
