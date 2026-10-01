#!/usr/bin/env python3
"""Build a whole episode: hook clip, middle with zooms and SFX, closer clip.

    python scripts/make_episode.py --episode <ep>/            # plan + free steps only
    python scripts/make_episode.py --episode <ep>/ --yes      # ...and spend

    python scripts/make_episode.py --episode <ep>/ --evergreen --yes   # no Veo at all

Either way the dry run prints an itemised bill for THIS episode before --yes, so the number
being approved is the number that gets spent.

Nobody supplies a screen recording, a terminal, or a cut. The only thing an episode
needs from a person is its four spoken lines, because that is authoring rather than
production -- everything downstream of them is mechanical and is done here.

The chain, and who does what:

    State 2   screen recording   make_screen_recording.py     free, automatic
    State 4   hook + closer      ab_generate.py, one wave     PAID
    States 5-10  screen, pick, trim, shake, VO, SFX, caption, stitch, verify
                                 finish-episode.py            PAID (voice clone, cents)

Both waves go out together: generating hook-then-closer instead of one wave is what
took the per-episode spend from $20.77 to about $7.50 (#22).

**Nothing is submitted without --yes.** Without it this prints the plan, the seeds and
the estimate, and still does every free step -- so the screen recording is real and
reviewable before a penny is spent. That is the lock order, not a formality: re-rolling
a paid render is the most expensive way to find out a line was wrong.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINES = ("hook_line", "vo_line_a", "vo_line_b", "closer_line")
COST_PER_CLIP = 0.90          # veo3.1 fast, 6s, 720p, measured on this series
SEEDS_PER_CLIP = 4            # four is enough with the twelve gates in place (#22)

# Everything paid that is NOT Veo, as a measured band rather than a per-unit guess.
# Derived by subtracting the $7.20 Veo wave from the totals TAKES.md actually recorded for
# the six episodes that needed no top-up: $7.23, $7.42, $7.43, $7.44, $7.51. That covers
# the voice clone (2 lines, re-rolled until they fit) and the speech-to-text calls -- eight
# in screen-takes, two in trim-tail, one on the finished cut in verify-episode.
COST_OTHER_LOW = 0.03
COST_OTHER_HIGH = 0.31
# The evergreen path generates nothing and screens nothing, so ten of those eleven
# transcription calls do not happen. Its band is the same one used as an UPPER bound,
# because the only honest thing to say without a measured evergreen run is "no more
# than this".
STT_CALLS_SAVED_BY_EVERGREEN = 10


def print_spend(evergreen: bool) -> None:
    """Say what THIS episode will cost, itemised, before --yes rather than after.

    The per-episode figure was previously a single sentence naming the Veo wave only,
    which is where the widely-quoted "$7.20 an episode" came from. The wave is $7.20; the
    episode is $7.23-$7.51, because the voice clone and the transcription calls are also
    paid. The gap is small, and quoting a number that excludes part of the bill is exactly
    how a format gets planned against the wrong budget.
    """
    clips = 0 if evergreen else 2 * SEEDS_PER_CLIP
    veo = clips * COST_PER_CLIP
    print("\n[episode] WHAT THIS EPISODE SPENDS")
    print("[episode]   %-34s %-12s %s"
          % ("screen recording (local capture)", "free", "$0.00"))
    if evergreen:
        print("[episode]   %-34s %-12s %s"
              % ("hook + closer (Veo 3.1 fast)", "REUSED", "$0.00"))
        print("[episode]   %-34s %-12s %s"
              % ("", "", "evergreen pair, generated once"))
    else:
        print("[episode]   %-34s %-12s $%.2f"
              % ("hook + closer (Veo 3.1 fast)", "%d x $%.2f" % (clips, COST_PER_CLIP), veo))
        print("[episode]   %-34s %-12s %s"
              % ("", "", "%d seeds x hook + closer, ONE wave (#22)" % SEEDS_PER_CLIP))
    label = "voice clone + transcription"
    if evergreen:
        print("[episode]   %-34s %-12s $%.2f - $%.2f  (upper bound)"
              % (label, "measured", COST_OTHER_LOW, COST_OTHER_HIGH))
        print("[episode]   %-34s %-12s %s"
              % ("", "", "%d of 11 transcription calls do not run on this path"
                 % STT_CALLS_SAVED_BY_EVERGREEN))
    else:
        print("[episode]   %-34s %-12s $%.2f - $%.2f"
              % (label, "measured", COST_OTHER_LOW, COST_OTHER_HIGH))
    print("[episode]   %-34s %-12s $%.2f - $%.2f"
          % ("TOTAL", "", veo + COST_OTHER_LOW, veo + COST_OTHER_HIGH))
    if not evergreen:
        # #27 measured roughly 1 in 6 and #51 measured 25-30% overall, both of which put a
        # four-seed wave short of a certainty. The six most recent recorded episodes each
        # came in at one wave per shot, so the figure above is what usually happens rather
        # than an expectation over re-rolls. Say which it is.
        print("[episode]   a shot where nothing passes needs another $%.2f wave; the last "
              "six\n[episode]   recorded episodes each needed none (#27, #51)"
              % (SEEDS_PER_CLIP * COST_PER_CLIP))


def resolve_reference(ep, spec, locked):
    """The image BOTH shots generate from: the episode's outfit variant, else the locked selfie.

    THIS WAS HARDCODED to locked/character-selfie.png and outfit_hook_ref was never read.
    The series changes outfit every other episode and every spec from episode 3 on names a
    variant, so the one-command path silently generated every episode in the default shirt.
    Episodes 1-10 shipped before this script existed and are unaffected. Episode 11 was the
    first built through it and shipped in the henley instead of the chambray its spec named:
    the garment STRING still reached the prompt, and Veo followed the image over the text.

    Both shots come from ONE image (#40), so outfit_closer_ref is only checked for agreement.
    A spec that names a non-default outfit but gives no image is an ERROR, not a fallback --
    falling back quietly to the locked selfie is exactly the bug this replaces.
    """
    series = ep.parent
    hook = spec.get("outfit_hook_ref")
    closer = spec.get("outfit_closer_ref")
    outfit = spec.get("outfit")
    if hook and closer and closer != hook:
        print("[episode] WARNING: outfit_closer_ref differs from outfit_hook_ref. Both shots "
              "generate from the hook image (#40); ignoring %s" % closer, file=sys.stderr)
    if hook:
        ref = Path(hook)
        return ref if ref.is_absolute() else series / ref
    if outfit and outfit != "locked":
        sys.exit("[episode] episode.json names outfit %r but has no outfit_hook_ref. Refusing "
                 "to fall back to the locked selfie: that fallback is how episode 11 shipped "
                 "in the wrong shirt." % outfit)
    return locked / "character-selfie.png"


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def run(cmd, label):
    print("\n[episode] %s\n          $ %s" % (label, " ".join(str(c) for c in cmd)))
    r = subprocess.run([str(c) for c in cmd])
    if r.returncode != 0:
        sys.exit("[episode] %s failed (exit %d)" % (label, r.returncode))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", required=True, type=Path)
    ap.add_argument("--locked", type=Path,
                    help="locked character dir (default: <episode>/../locked)")
    ap.add_argument("--garment", help="outfit string; defaults to the episode's own")
    ap.add_argument("--yes", action="store_true", help="submit the paid calls")
    ap.add_argument("--recapture", action="store_true",
                    help="shoot the screen recording again instead of reusing the "
                         "capture already made for this skill")
    ap.add_argument("--evergreen", nargs="?", const="", metavar="DIR",
                    help="reuse an approved hook/closer pair instead of generating one. "
                         "No Veo call is made at all. Bare --evergreen looks in "
                         "<series>/locked/evergreen; pass a directory to override. The "
                         "per-episode path stays the DEFAULT and is unchanged.")
    a = ap.parse_args()

    ep = a.episode
    spec_path = ep / "episode.json"
    if not spec_path.exists():
        sys.exit("no episode.json in %s" % ep)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    missing = [k for k in LINES if not spec.get(k)]
    if missing:
        sys.exit("[episode] episode.json is missing %s.\n"
                 "          These four lines are the one thing a person writes; "
                 "everything after them is automatic." % ", ".join(missing))

    locked = a.locked or (ep.parent / "locked")
    voice_ref = locked / "voice-ref.wav"          # the voice never changes with the outfit

    # The evergreen pair is a directory of finished clips, so nothing about this episode
    # resolves a reference image or a garment: both were fixed when the pair was promoted
    # and are recorded in its evergreen.json. Resolving them here would only invite an
    # outfit string that the reused clips cannot possibly be wearing.
    evergreen = None
    if a.evergreen is not None:
        evergreen = Path(a.evergreen) if a.evergreen else (locked / "evergreen")
        if not evergreen.exists():
            sys.exit("[episode] no evergreen pair at %s. Promote one with "
                     "promote-evergreen.py from an episode that already passed." % evergreen)

    part = spec.get("part_number", 0)
    base = 424000 + part * 100
    seeds = ",".join(str(base + i) for i in range(SEEDS_PER_CLIP))
    garment = a.garment or spec.get("garment")

    if evergreen:
        if not voice_ref.exists():
            sys.exit("[episode] missing asset: %s" % voice_ref)
        selfie = None
        print("[episode] evergreen pair:  %s" % evergreen)
        print("[episode] reference image: (not used; recorded in evergreen.json)")
    else:
        selfie = resolve_reference(ep, spec, locked)
        for p in (selfie, voice_ref):
            if not p.exists():
                sys.exit("[episode] missing asset: %s" % p)
        print("[episode] reference image: %s" % selfie)
        print("[episode] garment:         %s" % (garment or "(ab_generate default)"))

    # ---- State 2: the screen recording. Free, and it happens either way. -------------
    # Captured once, not once per invocation. new_episode.py runs this script without
    # --yes to get the plan and the recording, then a person runs it again WITH --yes;
    # re-capturing there cost five minutes and, worse, produced a different edit each
    # time -- 17.33s then 14.67s on part 2 -- which moved the notify value between two
    # builds of the same episode. Reuse it unless the featured skill changed.
    rec = ep / "screen-recording"
    marks_f = rec / "marks.json"
    reuse = False
    if marks_f.exists() and (rec / "zoom-edit.mp4").exists() and not a.recapture:
        try:
            prev = json.loads(marks_f.read_text(encoding="utf-8"))
            was = prev.get("skill_slug") or slugify(prev.get("skill"))
            reuse = bool(was) and was == (spec.get("featured_skill") or "")
        except Exception:
            reuse = False
    if reuse:
        print()
        print("[episode] State 2 - screen recording: reusing the capture already made "
              "for this skill. Pass --recapture to shoot it again.")
    else:
        run([sys.executable, HERE / "make_screen_recording.py",
             "--episode", spec_path, "--out", rec], "State 2 - screen recording")
    marks = json.loads((rec / "marks.json").read_text(encoding="utf-8"))
    notify = marks.get("notify_sec")
    print("\n[episode] screen recording: %s  %.2fs  skill=%s"
          % (rec / "zoom-edit.mp4", marks["duration_sec"], marks.get("skill")))
    print("[episode] notify cue: %s" % notify)
    if notify is None:
        print("[episode] the cue could not be measured; pin it by hand before the "
              "SFX pass (#46)", file=sys.stderr)

    # ---- State 4: the paid wave, or the evergreen pair instead -------------------------
    print_spend(evergreen)
    if not evergreen:
        print("[episode] seeds: %s" % seeds)
    if not a.yes:
        print("\n[episode] STOPPING before the paid calls. The screen recording above is "
              "real and reviewable.\n[episode] Re-run with --yes to %s"
              % ("generate the voiceover and finish the episode."
                 if evergreen else
                 "generate the character clips and finish the episode."))
        return

    if not evergreen:
        gen = [sys.executable, HERE / "ab_generate.py", "--engine", "veo",
               "--image", selfie, "--voice-ref", voice_ref, "--seeds", seeds,
               "--shake", "subtle", "--framing", "medium", "--duration", "6", "--yes"]
        if garment:
            gen += ["--garment", garment]
        # Both waves at once. They share the reference image, the seeds and the voice
        # reference, and neither reads the other's output -- but run in sequence the
        # closer only started once the hook had finished: 13.8 minutes, then 4 more.
        # Same spend, one wait. Each still writes its own out-dir and manifest.
        waves = [("hook", spec["hook_line"], ep / "hook"),
                 ("closer", spec["closer_line"], ep / "closer")]
        procs = []
        for label, line, out_dir in waves:
            cmd = [str(c) for c in gen + ["--line", line, "--out-dir", out_dir]]
            print()
            print("[episode] State 4 - %s wave submitted" % label)
            print("          $ %s" % " ".join(cmd))
            procs.append((label, subprocess.Popen(cmd)))
        failed = [label for label, pr in procs if pr.wait() != 0]
        if failed:
            sys.exit("[episode] State 4 failed: %s wave(s). Takes that did land are on "
                     "disk, and ab_generate does not re-charge for them on a re-run "
                     "(#117)." % ", ".join(failed))

    # ---- States 5-10: screen, pick, trim, shake, VO, SFX, caption, stitch, verify -----
    fin = [sys.executable, HERE / "finish-episode.py", "--episode", ep]
    if notify is not None:
        fin += ["--notify", str(notify)]
    if evergreen:
        fin += ["--evergreen", evergreen]
    run(fin, "States 5-10 - finish")

    print("\n[episode] done: %s" % ep)


if __name__ == "__main__":
    main()
