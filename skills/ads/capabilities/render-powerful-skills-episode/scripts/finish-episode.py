#!/usr/bin/env python3
"""Take an episode from generated batches to a verified final cut, in one command.

Everything between "the takes exist" and "the episode is done" is mechanical, and doing it
by hand is how episode 3 reached review with four defects. The order below is not
arbitrary -- several steps invalidate earlier ones if run out of sequence:

    1. screen      both batches. Shake is REPORTED but does not gate -- it is the
                   one criterion that can be supplied afterwards (#41)
    2. pick        prefer a pair that already has real shake, then the closest
                   voices among equals
    3. trim        each on its last scripted word, then on the picture (#31)
    4. shake       add it in post only if the picked take lacks it (#41)
    5. voice       generate VO from the clone and USE IT UNTOUCHED; match only the
                   VEO clips, and to the LOCKED series voice, never to this hook (#48)
    6. build       which trims the middle to the VO (#42) and runs the verifier (#44)

Match after adding shake: shake is a picture operation and does not touch audio, so the
order is free, but it is fixed so that what gets measured is what ships. The reference is
the LOCKED voice and never this episode's hook -- matching to the hook makes an episode
internally consistent while letting the series drift, which is how episode 3's VO ended up
further from the locked voice after being "matched" than before (#48).

This does NOT pick for performance. It picks for measurement and prints what it chose; if
the delivery is wrong, re-run with --hook-seed / --closer-seed to override.

With --evergreen, steps 1-4 and the timbre match do not run at all: an approved hook and
closer pair is copied in, sidecars and all, and the episode is finished around it. That is
only sound because the two clips are the only part of the picture that is paid, and because
the pair being reused went through steps 1-4 once already. See SCALE.md.

Usage:
    finish-episode.py --episode <dir> --notify 10.0
    finish-episode.py --episode <dir> --notify 10.0 --hook-seed 312 --closer-seed 425
    finish-episode.py --episode <dir> --evergreen <series>/locked/evergreen
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import pathlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _mod(name):
    import importlib.util as u
    spec = u.spec_from_file_location(name.replace("-", "_"), HERE / (name + ".py"))
    m = u.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(cmd, label, quiet=False):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       errors="replace")
    if not quiet:
        print((r.stdout or "").rstrip())
    if r.returncode != 0 and r.stderr:
        print("  (%s) %s" % (label, r.stderr.strip()[-300:]), file=sys.stderr)
    return r


def screen(ep: Path, shot: str, line: str, out_json: Path):
    d = ep / shot
    if not d.exists():
        sys.exit("ERROR: no %s batch in %s" % (shot, ep))
    # Shake is reported but does NOT gate here. It is the one criterion that can be
    # supplied afterwards (#41), so failing a take for it throws away good voice and
    # dialogue for something add-shake.py can add. Everything else is a hard fail.
    return run([sys.executable, HERE / "screen-takes.py", d,
                "--target-voice", ep.parent / "locked" / "character-voice.mp3",
                "--line", line, "--min-shake", "0",
                "--json", out_json], "screen " + shot)


def passes(js: Path):
    if not js.exists():
        return []
    return [r for r in json.loads(js.read_text(encoding="utf-8")) if r.get("pass")]


def install_evergreen(src: Path, F: Path, spec: dict) -> None:
    """Install an already-approved hook/closer pair in place of a generated batch.

    The hook and the closer are the ONLY paid video in this format -- the screen recording
    is captured locally from the site's own demo and the voiceover comes from the locked
    clone for cents (#38). Everything else about an episode already changes for free. So
    if the two spoken lines are written to be true of every episode, the two clips can be
    generated once and reused, and the per-episode video spend goes to zero.

    What this does NOT do is lower a bar. The pair being installed went through the whole
    per-episode path once: screened, picked, trimmed on its last word, tightened onto a
    settled last frame, shaken and timbre-matched. It is the same artefact the normal path
    produces, kept instead of thrown away. The sidecars come with it for that reason.
    """
    meta_path = src / "evergreen.json"
    if not meta_path.exists():
        sys.exit("ERROR: %s has no evergreen.json.\n"
                 "       Promote a finished pair with promote-evergreen.py rather than "
                 "copying files in by hand -- the metadata is what records which seeds "
                 "and which reference image the reused clips came from." % src)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # verify-episode.py transcribes the FINISHED cut and asserts that 80% of every
    # scripted line is audible in it. A spec whose lines disagree with the clips would
    # therefore fail at the very end of the run, one build and one paid transcription
    # later, reported as a dialogue failure rather than as the mismatch it is. The clips
    # cannot say a new line; say so here, before anything runs.
    for key in ("hook_line", "closer_line"):
        want = (meta.get(key) or "").strip()
        got = (spec.get(key) or "").strip()
        if want != got:
            sys.exit("ERROR: episode.json %s does not match the evergreen pair.\n"
                     "       evergreen: %r\n"
                     "       episode:   %r\n"
                     "       These clips are already generated and cannot speak a new "
                     "line. Either copy the evergreen lines into episode.json, or drop "
                     "--evergreen and build this episode on the per-episode path."
                     % (key, want, got))

    for name in ("hook.mp4", "closer.mp4"):
        s = src / name
        if not s.exists():
            sys.exit("ERROR: the evergreen pair is missing %s" % s)
        (F / name).write_bytes(s.read_bytes())
        # The sidecars are not spare copies. verify-episode measures the reference
        # correlation on `.orig` -- the UNTRIMMED take, because trimming the head breaks
        # that proxy (#65) -- and judges the last frame on `.flat`, the pre-shake clip,
        # because scale/crop/scale costs 0.014-0.033 of face correlation on its own (#49).
        # Without them those two gates measure the wrong picture, and a gate measuring the
        # wrong picture is worse than no gate.
        for ext in (".orig", ".flat"):
            side = src / (name + ext)
            if side.exists():
                (F / (name + ext)).write_bytes(side.read_bytes())
            else:
                print("  WARNING: the pair has no %s%s, so verify-episode will measure "
                      "that gate on the shipped clip instead of the right one (#65, #49)"
                      % (name, ext), file=sys.stderr)

    print("  hook.mp4 and closer.mp4 reused from %s" % src)
    print("  promoted %s from seeds hook=%s closer=%s, garment %r, reference %s"
          % (meta.get("promoted", "?"), meta.get("hook_seed", "?"),
             meta.get("closer_seed", "?"), meta.get("garment"),
             meta.get("reference_image", "?")))
    print("  hook:   %r" % meta.get("hook_line"))
    print("  closer: %r" % meta.get("closer_line"))
    print("  NO video generated for this episode. $0.00 of Veo.")


def balance_ok() -> tuple:
    """Read the fal balance before spending anything.

    An exhausted balance does not surface as a billing error -- it surfaces as a 403 on
    UPLOAD, which a wrapper that returns None on failure converts into a passed check.
    Episode 4's batches were screened with the dialogue criterion silently inactive that
    way. One cheap read here turns a half-screened batch into a clear stop.
    """
    import os, urllib.request, pathlib as _p
    key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY")
    if not key:
        env = _p.Path(".env")
        if env.exists():
            for ln in env.read_text(encoding="utf-8").splitlines():
                if ln.strip().startswith(("FAL_KEY", "FAL_API_KEY")) and "=" in ln:
                    key = ln.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key:
        return None, "no FAL key found; cannot check the balance"
    try:
        r = urllib.request.Request("https://rest.alpha.fal.ai/billing/user_balance",
                                   headers={"Authorization": "Key %s" % key})
        with urllib.request.urlopen(r, timeout=20) as resp:
            return float(resp.read().decode().strip()), None
    except Exception as e:                      # network, auth, endpoint change
        return None, "balance unreadable (%s)" % str(e)[:80]



def _line_end(clip: Path):
    """End of the last SPOKEN WORD of the scripted line, from ASR. None if unavailable."""
    try:
        import tempfile
        pb = _mod("pin-broll")
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "a.wav"
            r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-vn",
                                "-ac", "1", "-ar", "16000", str(wav), "-y"],
                               capture_output=True, text=True)
            if r.returncode or not wav.exists():
                return None
            ws = pb.words_of(wav)
        return float(ws[-1]["end"]) if ws else None
    except SystemExit:
        return None
    except Exception as exc:
        print("  (line-end transcript unavailable: %s)" % exc)
        return None


def _end_score(clip: Path, line_end):
    """Best last-frame score available in this take's tail, on verify-episode's measure.

    Whether a take ENDS well is a property of the take, and it is the one property that
    cannot be fixed afterwards -- shake can be added (#41), a music bed can be separated
    (#124), an ending cannot be invented. So it is measured before the pick, not after.
    Part 2 is why: both hook takes passed screening, the picker chose on shake (0.62 vs
    0.52) and took seed 424202, whose tail never scores above 0.57. The take it passed
    over scores 0.71-0.76 at every cut in its tail.
    """
    try:
        ve = _mod("verify-episode")
        d0 = ve.dur(clip)
        # Probe only the window that SURVIVES the trim. Scoring the whole tail rates
        # frames trim-tail has already cut away: part 3's hook scored 0.807 here and the
        # tighten step, working on the trimmed clip, found nothing above 0.674 -- because
        # the 0.807 frame sat inside the 0.72s of extra mouth movement after the line.
        # Ranking on a number measured outside the usable window is the same mistake as
        # #127, one level down (#134).
        floor_t = line_end if line_end else max(0.5, d0 - 0.5)
        ceiling = min(d0 - 0.05, floor_t + 0.45)
        probe = clip.parent / ("_es_" + clip.name)
        tmp = clip.parent / "_es.png"
        best, cut, n = None, ceiling, 0
        while cut >= floor_t and n < 7:
            r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-t", "%.3f" % cut,
                                "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
                                "-an", str(probe), "-y"], capture_output=True, text=True)
            if not r.returncode:
                s, _ = ve.last_frame_ok(ve.sh, probe, tmp)
                if s is not None and (best is None or s > best):
                    best = s
            cut -= 0.1
            n += 1
        probe.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        return best
    except Exception as exc:
        print("  (end-score unavailable for %s: %s)" % (clip.name, exc))
        return None

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episode", required=True, type=Path)
    ap.add_argument("--notify", type=float,
                    help="edit-relative seconds. OMIT IT: pin-notify.py finds the "
                         "deliverable automatically and writes proof frames to review/. "
                         "Pass it only to override a wrong answer (#58).")
    ap.add_argument("--hook-dir", default="hook")
    ap.add_argument("--closer-dir", default="closer")
    ap.add_argument("--hook-seed")
    ap.add_argument("--closer-seed")
    ap.add_argument("--whoosh-times")
    ap.add_argument("--evergreen", type=Path, metavar="DIR",
                    help="reuse an approved hook/closer pair from DIR instead of "
                         "screening, picking, trimming, shaking and matching a fresh "
                         "batch. Steps 1-4 are skipped entirely; the voiceover, the "
                         "screen recording, the SFX, the caption and the verify all "
                         "still run per episode. DIR is made by promote-evergreen.py.")
    args = ap.parse_args()

    ep = args.episode
    spec = json.loads((ep / "episode.json").read_text(encoding="utf-8"))
    part = spec["part_number"]
    F = ep / "final"
    F.mkdir(parents=True, exist_ok=True)

    bal, why = balance_ok()
    if bal is not None:
        print("fal balance: %.2f" % bal)
        if bal <= 0:
            sys.exit("ERROR: fal balance is %.2f -- the account is locked. Generation, the "
                     "voice clone and transcription will all 403. Top up before running."
                     % bal)
    else:
        print("fal balance: %s -- continuing, but a 403 mid-run means this" % why)

    review = ep / "review"
    review.mkdir(parents=True, exist_ok=True)
    notify = args.notify
    if notify is None:
        edit = ep / "screen-recording" / "zoom-edit.mp4"
        r = run([sys.executable, HERE / "pin-notify.py", "--edit", edit,
                 "--out-dir", review], "pin-notify")
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("--notify "):
                notify = float(ln.split()[1])
        if notify is None:
            sys.exit("ERROR: could not find the deliverable. Pin --notify by eye.")
        print("  auto-pinned --notify %.2f  (check review/cue-before.png and cue-at.png)"
              % notify)

    if args.evergreen:
        print("=== 1-4. evergreen hook and closer, nothing generated ===")
        install_evergreen(args.evergreen, F, spec)
        print("\n=== 5. voiceover from the clone (the only per-episode voice) ===")
    else:
        print("=== 1. screen both batches ===")
        hj, cj = F / "screen-hook.json", F / "screen-closer.json"
        screen(ep, args.hook_dir, spec["hook_line"], hj)
        screen(ep, args.closer_dir, spec["closer_line"], cj)
        hp, cp = passes(hj), passes(cj)

        # A take that fails ONLY on a baked-in music bed is not a re-roll. strip-music.py
        # separates the bed out and the take passes: on part 2 that rescued both surviving
        # closers and saved a $3.60 wave. A take failing on PITCH is a different thing and
        # is never repaired -- it is re-generated (#48).
        for label, d, line, js in (("hook", args.hook_dir, spec["hook_line"], hj),
                                   ("closer", args.closer_dir, spec["closer_line"], cj)):
            if passes(js):
                continue
            rows = json.loads(js.read_text(encoding="utf-8"))
            bedonly = [r for r in rows
                       if r.get("fails") and all("music" in f for f in r["fails"])]
            if not bedonly:
                continue
            print()
            print("=== 1b. %d %s take(s) fail only on a music bed; separating it out ==="
                  % (len(bedonly), label))
            clean = ep / (d + "-nomusic")
            run([sys.executable, HERE / "strip-music.py"]
                + [r["path"] for r in bedonly] + ["--out-dir", clean], "strip " + label)
            screen(ep, clean.name, line, js)
        hp, cp = passes(hj), passes(cj)

        if not hp or not cp:
            print("\nNothing passed in %s. Generate more seeds rather than lowering a bar."
                  % ("both batches" if not hp and not cp else
                     ("the hook batch" if not hp else "the closer batch")), file=sys.stderr)
            return 1

        print("\n=== 2. pick the pair with the closest voices ===")

        def seed_of(r):
            return "".join(ch for ch in Path(r["clip"]).stem if ch.isdigit())

        if args.hook_seed:
            hp = [r for r in hp if seed_of(r) == args.hook_seed] or hp
        if args.closer_seed:
            cp = [r for r in cp if seed_of(r) == args.closer_seed] or cp
        # Prefer a pair that already has real shake -- a genuine handheld take always beats a
        # synthesised one -- then, among equals, the closest voices.
        def shaky(r):
            # screen-takes reports the MEDIAN, and so does verify-episode's gate. Preferring on
            # p90 here would rank a frozen take with occasional snaps above a genuinely
            # handheld one -- the exact confusion that shipped episodes 3 and 4 locked off.
            return (r.get("shake") or 0) >= 0.45

        def has_tail(r):
            # A take whose line runs to the last frame cannot be trimmed and cannot end on a
            # settle -- Veo paces dialogue to fill the requested duration (#24), so some takes
            # come back with no tail at all. Episode 4's first pick had 0.04s and ended
            # mid-drift; another take in the same batch had 0.90s. This is the FIRST thing to
            # sort on, because unlike shake it cannot be fixed afterwards.
            return (r.get("tail") or 0) >= 0.45

        # How well a take ENDS is measured here, before the pick, on the same measure
        # verify-episode asserts. It ranks above shake because shake can be added
        # afterwards and an ending cannot (#127).
        for r in hp + cp:
            if r.get("end_score") is None:
                le = _line_end(Path(r["path"]))
                r["end_score"] = _end_score(Path(r["path"]), le)
                print("  %-22s ends at %s"
                      % (Path(r["path"]).name,
                         ("%.3f" % r["end_score"]) if r["end_score"] is not None else "?"))

        def ends_well(r):
            return r.get("end_score") or 0.0

        def score(h, c):
            return (-(has_tail(h) + has_tail(c)),
                    -round(ends_well(h) + ends_well(c), 2),
                    -(shaky(h) + shaky(c)),
                    abs((h.get("f0") or 0) - (c.get("f0") or 0)))

        H, C = min(((h, c) for h in hp for c in cp), key=lambda p_: score(*p_))
        gap = abs((H.get("f0") or 0) - (C.get("f0") or 0))
        print("  hook seed %s (F0 %.1f, shake %.2f) + closer seed %s (F0 %.1f, shake %.2f)"
              % (seed_of(H), H.get("f0") or 0, H.get("shake") or 0,
                 seed_of(C), C.get("f0") or 0, C.get("shake") or 0))
        print("  voices %.1f Hz apart; %d/2 have real shake; tails %.2fs and %.2fs"
              % (gap, int(shaky(H)) + int(shaky(C)), H.get("tail") or 0, C.get("tail") or 0))
        for src, name in ((Path(H["path"]), "hook.mp4"), (Path(C["path"]), "closer.mp4")):
            (F / name).write_bytes(src.read_bytes())

        # Keep the UNTRIMMED take. The reference check compares first frames and its bar was
        # calibrated on untrimmed clips, so any head trim invalidates it (#65).
        for _n in ("hook.mp4", "closer.mp4"):
            _c = F / _n
            if _c.exists():
                (F / (_n + ".orig")).write_bytes(_c.read_bytes())

        print("\n=== 3. trim each on its last scripted word ===")
        for name, key in (("hook.mp4", "hook_line"), ("closer.mp4", "closer_line")):
            run([sys.executable, HERE / "trim-tail.py", F / name, "--line", spec[key],
                 "--out-dir", F / "_trim"], "trim " + name)
            t = F / "_trim" / name
            if t.exists():
                (F / name).write_bytes(t.read_bytes())
        import shutil
        shutil.rmtree(F / "_trim", ignore_errors=True)

        print("\n=== 3b. tighten each cut until the last frame faces the lens ===")
        # trim-tail chooses the cut from its own face-PSNR threshold, and that threshold has
        # been too loose four times running -- the clip ends on a look-away or a drifting
        # smile. Rather than tune it again, walk the cut back until the frame scores against
        # the SAME measure verify-episode.py asserts. The trimmer and the check can then never
        # disagree, which was the real bug: two thresholds for one property.
        ve = _mod("verify-episode")
        for name, row in (("hook.mp4", H), ("closer.mp4", C)):
            clip = F / name
            if not clip.exists():
                continue
            sp = row.get("speech")
            # The floor is where the LINE ends, not where the sound ends. Veo paces the
            # line to fill the clip and then keeps the mouth moving: on part 2 every hook
            # take "spoke" to 5.8 of a 6.02s clip, so a speech-end floor left nothing to
            # cut and the episode shipped a hook that talks on past its last word. ASR
            # transcribes the scripted words and not the mumble, so its last word end is
            # the real floor. Fall back to speech-end only if the transcript fails.
            floor_t = _line_end(clip)
            if floor_t is None:
                floor_t = (sp[1] + 0.15) if sp else 0.5
            else:
                print("  %s line ends %.2fs (speech ran to %.2fs)"
                      % (name, floor_t, sp[1] if sp else -1))
            tmp = F / "_ef.png"
            d0 = ve.dur(clip)
            cut, best, best_any = d0, None, None
            while cut > floor_t:
                probe = F / ("_probe_" + name)
                run(["ffmpeg", "-v", "error", "-i", clip, "-t", "%.3f" % cut,
                     "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000", probe, "-y"],
                    "probe", quiet=True)
                s, _ = ve.last_frame_ok(ve.sh, probe, tmp)
                # Remember the best frame seen even when nothing clears the bar. Some
                # takes never reach it -- part 2's hooks topped out at 0.72 -- and
                # "leave it as is" then keeps the WORST option, the untouched trim-tail
                # cut, which on that episode scored 0.515 against the 0.724 available
                # two tenths earlier. Best-available beats unchanged (#126).
                if s is not None and (best_any is None or s > best_any[1]):
                    best_any = (cut, s, probe.read_bytes())
                if s is not None and s >= ve.END_FACE + 0.02:
                    best = (cut, s, probe.read_bytes())
                    probe.unlink(missing_ok=True)
                    break
                probe.unlink(missing_ok=True)
                # 0.05, not 0.1: on part 2 the only frame where the mouth was
                # closed and the eyes were on the lens sat within a tenth of the
                # last word, and a coarser step stepped straight over it.
                cut -= 0.05
            tmp.unlink(missing_ok=True)
            if best is None and best_any is not None and abs(best_any[0] - d0) >= 0.05:
                clip.write_bytes(best_any[2])
                print("  %-11s nothing above %.2f; kept the BEST frame instead, "
                      "%.2fs -> %.2fs (%.3f)"
                      % (name, ve.END_FACE, d0, best_any[0], best_any[1]))
            elif best is None:
                print("  %-11s nothing above %.2f down to the line end -- left as is"
                      % (name, ve.END_FACE))
            elif abs(best[0] - d0) < 0.05:
                print("  %-11s %.2fs already ends facing the lens (%.3f)" % (name, d0, best[1]))
            else:
                clip.write_bytes(best[2])
                print("  %-11s %.2fs -> %.2fs, last frame %.3f" % (name, d0, best[0], best[1]))


        print("\n=== 4. add shake if the picked take lacks it ===")
        run([sys.executable, HERE / "add-shake.py", F / "hook.mp4", F / "closer.mp4"], "shake")
        # KEEP the .flat sidecars. verify-episode judges the last frame on them: shake is a
        # camera move added afterwards, and putting the frame through scale/crop/scale costs
        # 0.014-0.033 of face correlation for reasons that have nothing to do with whether the
        # performance drifted. Measuring the flat clip isolates the defect from the move (#49).

        print("\n=== 5. voiceover, then match the VEO clips to the LOCKED series voice ===")
        # Two rules here, both bought the hard way (#48).
        #
        # The reference is the LOCKED voice, not this episode's hook. The hook is a fresh
        # generation that varies per episode -- episode 3's measured 0.901 against the locked
        # voice -- so matching to it drags each episode toward its own hook and the series
        # drifts. Episode 3's VO went from 0.963 to 0.914 against the locked voice by being
        # "matched". One fixed reference for all ten episodes is the whole point.
        #
        # The VO is NOT a target. It comes from the locked clone, so it is already the series
        # voice; correcting it can only move it away. Only the Veo audio -- which is re-rolled
        # every shot and genuinely varies -- gets corrected. On the evergreen path there is
        # no fresh Veo audio at all, which is why the match step below drops out entirely.
    run([sys.executable, HERE / "make-vo.py", "--episode", ep], "vo")
    if args.evergreen:
        # The pair was matched to the locked voice ONCE, when it was promoted, and
        # verified there. Re-running the EQ every episode would apply it again to
        # already-corrected audio, and #61 is explicit that matching a clip which
        # already clears the floor can push it OUT of the pitch tolerance. Reuse means
        # reusing the corrected file, not re-correcting it.
        print("  evergreen pair already matched at promotion time -- not re-matching")
    else:
        # Only correct what is actually off. The EQ moves the measured F0 as a side effect, so
        # matching a clip that already clears the timbre floor can push it OUT of the pitch
        # tolerance: episode 6's closer went 0.951 timbre / -7.3 Hz (both passing) to 0.981 /
        # -10.2 Hz (pitch FAILING). Leave a passing clip alone (#61).
        mvt = _mod("match-voice-timbre")
        m_ = mvt._st()
        lock = ep.parent / "locked" / "character-voice.mp3"
        R_, _ = mvt.ltas(m_, lock)
        targets = []
        for name in ("hook.mp4", "closer.mp4"):
            clip = F / name
            if not clip.exists():
                continue
            T_, _ = mvt.ltas(m_, clip)
            sim = mvt.sim(R_, T_) if T_ is not None else 0.0
            if sim >= 0.946:
                print("  %-11s timbre %.3f already clears the floor -- left alone" % (name, sim))
            else:
                targets.append(clip)
        if targets:
            cmd_ = [sys.executable, HERE / "match-voice-timbre.py", "--ref", lock]
            for t_ in targets:
                cmd_ += ["--target", t_]
            run(cmd_, "match")

    # --- 5b. the v3 results section -------------------------------------------------
    # The payoff used to be assembled by hand after this script finished, which is how an
    # episode shipped with the notify value missing from episode.json and its gate
    # silently skipped. It belongs here, with everything else the episode needs.
    results = vo_c = None
    if (F / "vo-c.mp3").exists():
        work = ep / "working"
        choices = (("board-spec.json", "make-board-broll.py"),
                   ("sheet-spec.json", "make-sheet-broll.py"),
                   ("report-spec.json", "make-report-broll.py"))
        spec_f = tool = None
        for fname, script in choices:
            if (work / fname).exists():
                spec_f, tool = work / fname, script
                break
        if spec_f is None:
            print()
            print("[results] vo-c.mp3 exists but neither working/sheet-spec.json nor")
            print("          working/report-spec.json nor working/board-spec.json")
            print("          chosen from what the skill OUTPUTS, and it is shown before")
            print("          it is rendered, so that file is written by hand. See")
            print("          SKILL.md, 'The payoff visual'. Building without a results")
            print("          section.")
        else:
            print()
            print("=== 5b. results b-roll (%s) ===" % spec_f.name)
            results, vo_c = work / "results.mp4", F / "vo-c.mp3"
            run([sys.executable, HERE / tool, "--spec", spec_f, "--vo", vo_c,
                 "--out", results, "--still", review / "broll-still.png"], "results")

    # --- 5c. re-time the edit to the voice --------------------------------------------
    # The middle holds to notify + 1.0s so the deliverable stays on screen (#82). When the
    # VO is shorter than that the terminal runs on with nobody talking: 2.8s of it on part
    # 2. Re-time the PICTURE and scale notify by the same factor, never pad the voice (#85).
    edit = ep / "screen-recording" / "zoom-edit.mp4"

    def _dur(q):
        # Say WHICH file is missing. The voice step died once on an HTTP 500 from the TTS
        # provider, and the next thing anyone saw was "could not convert string to float:
        # ''" from here, three steps away from the actual failure (#135).
        if not pathlib.Path(q).exists():
            sys.exit("ERROR: %s was never written. The step that produces it failed "
                     "earlier in this run -- scroll up for its error." % q)
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", str(q)],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            sys.exit("ERROR: ffprobe could not read a duration from %s; the file is "
                     "there but empty or truncated." % q)
        return float(out)

    vo_len = _dur(F / "vo-a.mp3") + _dur(F / "vo-b.mp3")
    want_notify = vo_len + 0.35 - 1.0        # build-episode's --hold and DELIVERABLE_HOLD
    factor = want_notify / float(notify)
    if factor < 0.995:
        if factor < 0.70:
            print("[retime] wanted x%.3f, clamped to 0.70: past that the locked camera "
                  "beats stop reading" % factor)
            factor = 0.70
        retimed = ep / "screen-recording" / "zoom-edit-retimed.mp4"
        run(["ffmpeg", "-v", "error", "-i", edit, "-filter:v", "setpts=PTS*%.4f" % factor,
             "-an", "-c:v", "libx264", "-crf", "16", "-preset", "slow",
             "-pix_fmt", "yuv420p", retimed, "-y"], "retime")
        if _dur(retimed) < vo_len:
            sys.exit("ERROR: the re-timed edit (%.2fs) is shorter than the VO (%.2fs). "
                     "Re-roll the VO shorter; do not stretch the picture."
                     % (_dur(retimed), vo_len))
        print("[retime] edit %.2f -> %.2fs (x%.4f), notify %.3f -> %.3f"
              % (_dur(edit), _dur(retimed), factor, float(notify),
                 float(notify) * factor))
        edit, notify = retimed, "%.3f" % (float(notify) * factor)

    # verify-episode reads the notify gate's value from episode.json. Without it the gate
    # is skipped in silence and the run still says "all checks passed" -- on eleven gates,
    # not twelve. Write it where the verifier looks.
    _sp = ep / "episode.json"
    _spec = json.loads(_sp.read_text(encoding="utf-8"))
    if _spec.get("notify") != float(notify):
        _spec["notify"] = float(notify)
        _sp.write_text(json.dumps(_spec, indent=2, ensure_ascii=False) + chr(10),
                       encoding="utf-8")
        print("[notify] wrote %.3f into episode.json for the verifier" % float(notify))

    print("\n=== 6. build and verify ===")
    cmd = [sys.executable, HERE / "build-episode.py", "--part", part,
           "--hook", F / "hook.mp4", "--closer", F / "closer.mp4",
           "--vo-a", F / "vo-a.mp3", "--vo-b", F / "vo-b.mp3",
           "--zoom-edit", edit,
           "--notify", notify,
           "--out", ep / "output" / ("episode-%d-final.mp4" % part)]
    if results:
        cmd += ["--results", results, "--vo-c", vo_c,
                "--dur-range", "22:34"]
    if args.whoosh_times:
        cmd += ["--whoosh-times", args.whoosh_times]
    r = run(cmd, "build")

    # ONE place to look. The three things no measurement can settle are the two clip
    # endings and whether the cue is on the deliverable -- each has been reported by the
    # operator more than once, and each was reported because there was nothing to look at
    # until the finished episode existed (#58).
    out_dir = ep / "output"
    for src, dst in (("end-hook.png", "end-hook.png"),
                     ("end-closer.png", "end-closer.png"),
                     ("lips-hook.png", "lips-hook.png"),
                     ("lips-closer.png", "lips-closer.png"),
                     ("framing-hook.png", "framing-hook.png"),
                     ("framing-closer.png", "framing-closer.png")):
        if (out_dir / src).exists():
            (review / dst).write_bytes((out_dir / src).read_bytes())
    print("\n=== REVIEW THESE BEFORE SENDING (nothing above can judge them) ===")
    for f in ("end-hook.png", "end-closer.png", "lips-hook.png", "lips-closer.png",
              "framing-hook.png", "framing-closer.png", "cue-before.png", "cue-at.png"):
        if (review / f).exists():
            print("  %s" % (review / f))
    print("  endings: settled, eyes open, facing the lens across the WHOLE strip")
    print("  lips:    lip line and teeth stay defined -- Veo smears them on some seeds")
    print("  framing: he stays the SAME SIZE -- the other two strips are crops and")
    print("           cannot show a push-in (#71)")
    print("  cue:     chip absent in cue-before, present in cue-at")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
