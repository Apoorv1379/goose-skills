#!/usr/bin/env python3
"""Produce an episode's screen recording. No human, no screen recorder, no terminal.

    python scripts/make_screen_recording.py --episode <ep>/episode.json --out <ep>/screen-recording/
    python scripts/make_screen_recording.py --skill "Technical SEO audit" --out work/

The only per-episode input is WHICH SKILL. Everything else -- the choreography, the
browser chrome, the timing, the notify cue -- is fixed by the defaults in `defaults/`,
which were measured off the finished episodes rather than designed.

The skill is resolved by slug against the live site, so an episode's `featured_skill`
("technical-seo-audit") finds "Technical SEO audit" with no lookup table to maintain.
Set `screen_skill` in episode.json only when the two genuinely differ -- episode 2's
featured_skill is the tool name `goose-graphics`, whose demo on the site is called
"Make a LinkedIn carousel".

Output: <out>/zoom-edit.mp4 and <out>/marks.json, where `notify_sec` is the deliverable
measured off the render.

THE EDIT'S LENGTH IS NOT FREE -- IT HAS TO FIT THE VOICEOVER (#85).
This produces an edit at whatever pace the site's demo happens to run, and nothing here
knows how long the episode's voiceover is. When the demo is LONGER, `build-episode.py`
still holds the middle open so the deliverable stays on screen (#82), and the difference
plays as terminal running with nobody talking -- episode 11 shipped a first cut with 4.30
seconds of it. The fix today is applied after the fact, to this file's own output:

    ffmpeg -i zoom-edit.mp4 -vf "setpts=PTS/1.30" -r 30 -c:v libx264 -crf 17         -preset slow zoom-edit-130.mp4          # and scale --notify by the same 1.30

That works, but it is a repair. The tempo belongs HERE, where the deliverable time is
already measured: given a target middle length this step could emit the edit at the right
pace and leave nothing to catch. See proposal 10 in the project's skill-update-plan.md.
Do not close the gap from the other side by writing more voiceover -- the script sets the
length and the demo follows it.
"""
import argparse, json, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--episode", help="path to an episode.json")
    g.add_argument("--skill", help="skill name or slug, e.g. 'Technical SEO audit'")
    ap.add_argument("--out", required=True)
    ap.add_argument("--beats", default=str(ROOT / "defaults" / "screen-beats.json"))
    ap.add_argument("--camera", default=str(ROOT / "defaults" / "camera.json"))
    ap.add_argument("--keep-plate", action="store_true",
                    help="keep the raw plate (it is large; deleted by default)")
    a = ap.parse_args()

    if a.episode:
        ep = json.loads(Path(a.episode).read_text(encoding="utf-8"))
        skill = ep.get("screen_skill") or ep.get("featured_skill")
        if not skill:
            sys.exit("episode.json has neither screen_skill nor featured_skill")
        # Aliases live HERE, in the skill, not in an episode.json under the gitignored
        # projects/ tree -- otherwise the one mapping that cannot be derived exists only
        # on the machine that first needed it, and the next person's run fails with
        # "no skill matching goose-graphics".
        if not ep.get("screen_skill"):
            aliases = json.loads((ROOT / "defaults" / "skill-aliases.json")
                                 .read_text(encoding="utf-8"))
            skill = aliases.get(skill, skill)
    else:
        skill = a.skill

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    spec = json.loads(Path(a.beats).read_text(encoding="utf-8"))
    spec["skill"] = skill
    spec_path = out / "screen-beats.json"
    spec_path.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")

    plate = out / "plate"
    print("[make] capturing the plate for %r" % skill)
    subprocess.run(["node", str(HERE / "record-screen-beats.js"), str(spec_path), str(plate)],
                   check=True)

    print("[make] moving the camera")
    subprocess.run([sys.executable, str(HERE / "apply_camera.py"), str(plate), a.camera,
                    "--out", str(out / "zoom-edit.mp4")], check=True)

    marks = json.loads((out / "marks.json").read_text(encoding="utf-8"))
    meta = json.loads((plate / "plate.json").read_text(encoding="utf-8"))
    marks["skill"] = meta.get("skill")
    marks["category"] = meta.get("category")
    (out / "marks.json").write_text(json.dumps(marks, indent=1), encoding="utf-8")

    if not a.keep_plate:
        shutil.rmtree(plate, ignore_errors=True)

    print("\n[make] %s  %.2fs" % (out / "zoom-edit.mp4", marks["duration_sec"]))
    print("[make] skill:  %s  (category: %s)" % (marks["skill"], marks["category"]))
    print("[make] notify: %s   <- pass this to build-episode.py --notify" % marks["notify_sec"])
    # The edit has to fit the voiceover. Nothing here knows the VO length, so this is a
    # reminder rather than a check -- see #85 and the module docstring.
    print("[make] this edit is %.2fs. If the VO comes in much shorter, the middle will "
          "run on with nobody talking -- re-time the edit, do NOT pad the VO (#85)."
          % marks["duration_sec"])


if __name__ == "__main__":
    main()
