#!/usr/bin/env python3
"""Keep a finished hook/closer pair so later episodes can reuse it instead of paying again.

The hook and the closer are the only PAID picture in this format. The screen recording is
captured locally from the site's own demo and the voiceover comes from the locked clone for
cents (#38), so if the two spoken lines are written to be true of EVERY episode, the two
clips are generated once and the per-episode video spend is zero. That is the whole of the
evergreen route; this script is the one step that makes it possible.

It does not generate anything and it does not judge anything. It copies an episode's
already-approved `final/hook.mp4` and `final/closer.mp4` -- screened, picked, trimmed on
the last scripted word, tightened onto a settled last frame, shaken and timbre-matched --
into a directory, with their sidecars and a record of where they came from.

THE SIDECARS ARE NOT OPTIONAL. `verify-episode.py` measures the reference correlation on
`.orig`, the untrimmed take, because trimming the head breaks that proxy (#65), and judges
the last frame on `.flat`, the pre-shake clip, because scale/crop/scale costs 0.014-0.033 of
face correlation by itself (#49). A pair promoted without them makes both gates measure the
wrong picture on every episode that reuses it, forever, which is worse than having no gate.

WHAT THIS DOES NOT CHECK, and cannot. Whether the lines are genuinely evergreen is an
authoring judgement: a line naming a skill, a number or a command will be false on the
second episode and no measurement here can see that. Read the lines. And the four things
SKILL.md says no measurement replaces -- the settled ending, the lip definition, the
framing hold, the delivery -- are now being approved once for every future episode rather
than once for one, so look at the review strip before promoting, not after.

Usage:
    promote-evergreen.py --from <ep>/ --out <series>/locked/evergreen
    promote-evergreen.py --from <ep>/ --out <series>/locked/evergreen --force
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

CLIPS = ("hook.mp4", "closer.mp4")
SIDECARS = (".orig", ".flat")


def dur(p: Path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True)
        return round(float(r.stdout.strip()), 2)
    except Exception:                                   # no ffprobe, unreadable file
        return None


def seed_of(ep: Path, shot: str):
    """The seed the shipped clip came from, read back from the batch's own manifest.

    Recorded because a reused clip is now a series asset and #13 applies to it more than to
    anything else here: without the seed there is no way to regenerate it if the file is
    ever lost, and seeds in this pipeline are deterministic.
    """
    man = ep / shot / "manifest.json"
    if not man.exists():
        return None
    try:
        m = json.loads(man.read_text(encoding="utf-8"))
    except Exception:
        return None
    # Match on the `.orig` sidecar, not on the shipped clip. `.orig` is a byte copy of the
    # picked take made before any trim (#65), so its size identifies which take was picked;
    # the shipped clip has been trimmed and shaken and matches nothing in the batch.
    # And match by BASENAME under ep/<shot>/: the manifest stores paths relative to the
    # repo root, so resolving them as written depends on the caller's working directory.
    src = ep / "final" / (shot + ".mp4.orig")
    size = src.stat().st_size if src.exists() else None
    if size:
        for t in m.get("takes", []):
            if t.get("status") != "ok" or not t.get("file"):
                continue
            cand = ep / shot / Path(t["file"]).name
            if cand.exists() and cand.stat().st_size == size:
                return t.get("seed")
    ok = [t.get("seed") for t in m.get("takes", []) if t.get("status") == "ok"]
    if len(ok) == 1:
        return ok[0]
    # Batches get cleaned out after an episode ships, so the take that was picked is often
    # the only one still on disk. Say it is a guess rather than reporting it as read.
    live = [t.get("seed") for t in m.get("takes", []) if t.get("status") == "ok"
            and (ep / shot / Path(t.get("file", "x")).name).exists()]
    if len(live) == 1:
        return "%s?" % live[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True, type=Path,
                    help="the episode directory whose final/ pair is being kept")
    ap.add_argument("--out", required=True, type=Path,
                    help="where the pair lives. <series>/locked/evergreen is what "
                         "make_episode.py --evergreen looks for with no argument.")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing pair. Every episode built on the old one "
                         "stays as it shipped, but every FUTURE one changes.")
    a = ap.parse_args()

    ep = a.src
    spec_path = ep / "episode.json"
    if not spec_path.exists():
        sys.exit("ERROR: no episode.json in %s" % ep)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    final = ep / "final"

    missing = [n for n in CLIPS if not (final / n).exists()]
    if missing:
        sys.exit("ERROR: %s has no %s. Promote an episode that FINISHED, not one that "
                 "errored: a build that fails leaves the previous render in place and the "
                 "verifier will happily pass it (#54)." % (final, ", ".join(missing)))

    if a.out.exists() and (a.out / "evergreen.json").exists() and not a.force:
        sys.exit("ERROR: %s already holds a pair. Replacing it changes every FUTURE "
                 "episode, so it needs --force said out loud." % a.out)

    a.out.mkdir(parents=True, exist_ok=True)
    for n in CLIPS:
        (a.out / n).write_bytes((final / n).read_bytes())
        for ext in SIDECARS:
            s = final / (n + ext)
            if s.exists():
                (a.out / (n + ext)).write_bytes(s.read_bytes())
            else:
                print("WARNING: %s has no %s%s. Promoting without it makes verify-episode "
                      "measure the reference correlation or the last frame on the wrong "
                      "picture (#65, #49) on EVERY episode that reuses this pair."
                      % (final, n, ext), file=sys.stderr)

    meta = {
        "promoted": _dt.date.today().isoformat(),
        "promoted_from": str(ep),
        "hook_line": spec.get("hook_line"),
        "closer_line": spec.get("closer_line"),
        "garment": spec.get("garment"),
        "reference_image": spec.get("outfit_hook_ref") or "locked/character-selfie.png",
        "hook_seed": seed_of(ep, "hook"),
        "closer_seed": seed_of(ep, "closer"),
        "hook_sec": dur(a.out / "hook.mp4"),
        "closer_sec": dur(a.out / "closer.mp4"),
        "note": ("These two clips are reused by every episode built with --evergreen. They "
                 "are only valid while the lines above are true of any featured skill, and "
                 "they must be regenerated if the character, the outfit or the series "
                 "branding changes. See SCALE.md."),
    }
    (a.out / "evergreen.json").write_text(json.dumps(meta, indent=2) + "\n",
                                          encoding="utf-8")

    print("promoted %s -> %s" % (ep, a.out))
    for k in ("hook_line", "closer_line", "hook_seed", "closer_seed",
              "hook_sec", "closer_sec", "garment"):
        print("  %-13s %s" % (k, meta[k]))
    print("\nEvery episode using this pair speaks those two lines. If either one names a "
          "skill,\na number or a command, it is not evergreen and the second episode will "
          "lie.")
    print("Next: python scripts/make_episode.py --episode <ep>/ --evergreen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
