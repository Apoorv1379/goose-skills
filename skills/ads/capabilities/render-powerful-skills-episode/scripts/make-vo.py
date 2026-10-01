#!/usr/bin/env python3
"""Generate an episode's two voiceover lines from the LOCKED voice clone (State 6).

WHY TTS AND NOT VEO
Critical knowledge #15b rejected the TTS route, but that rejection was about lip-sync onto
a talking-head picture. The voiceover plays over the screen recording with NO FACE on
frame, so there is nothing to sync and the objection does not apply here.

Generating VO on Veo means the voice is re-picked every time. Measured: episode 3's first
VO take came back at 152.4 Hz against a 136.8 Hz target -- +15.6, nearly double the +/-8
tolerance -- so VO would need six to twelve seeds per line, at $0.90 each, and a screening
pass. The clone is deterministic, costs cents, and needs no screening:

    Veo VO take (seed 501)   152.4 Hz   +15.6   <- reject
    TTS clone at speed 1.4   138.5 Hz    +1.7   <- and identical every run

THE CLONE'S OUTPUT IS NEVER PITCH-SHIFTED. It used to be, toward an F0 target, and that
was the single biggest source of "the voice sounds different" in this series. The target
came from an autocorrelation median that octave-errors by 5-25 Hz depending on the take, so
the corrections were chasing a measurement artefact; worse, a second stage in
match-voice-timbre.py shifted again on top, and the compounded resample replaced the voice.
Episodes 1 and 2 -- the ones that sounded right -- applied no shift and no EQ at all. So a
line is now SCREENED on timbre against the locked voice and either used untouched or
re-rolled. See critical knowledge #48.

SPEED IS NOT OPTIONAL. At the default 1.0 the clone reads 1.79 words/sec, against the
2.91 that episodes 1 and 2 shipped -- a 19-word line runs 10.6s instead of 6.8s, which
does not fit the screen recording. 1.4 measures 2.81 w/s and is the locked value. Speed
does move the measured pitch a little (1.4 -> +1.7 Hz, 1.6 -> -4.0), so re-check F0 if you
ever change it.

Usage:
    make-vo.py --episode projects/<char>/episode-3
    make-vo.py --episode ... --speed 1.4 --check-fit
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

MODEL = "fal-ai/minimax/speech-2.8-hd"
SPEED = 1.4          # 2.81 words/sec, matching the shipped episodes
F0_TOL = 8.0       # the same bar verify-episode.py gates on (#128)
TIMBRE_FLOOR = 0.945 # LTAS correlation against the locked voice. Episodes 1-2 shipped
                     # 0.947-0.963 with NO processing at all; that is the bar.


def dur(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def f0_of(p: Path) -> float | None:
    """Measured with screen-takes' own code path, never a second implementation.

    Two scripts measuring F0 over different windows disagreed by 17 Hz once and rejected a
    take that had already shipped.
    """
    import importlib.util as u
    here = Path(__file__).resolve().parent
    spec = u.spec_from_file_location("st", here / "screen-takes.py")
    m = u.module_from_spec(spec)
    spec.loader.exec_module(m)
    x, sr = m.audio(p)
    return m.f0_median(x, sr) if x is not None else None


def timbre_vs(ref: Path, tgt: Path) -> float | None:
    """LTAS correlation against the locked series voice -- the SCREEN, not a correction.

    This replaced pitch matching entirely. See the module docstring: the clone's output is
    the series voice by definition, so a take is either good enough to use untouched or it
    is a reject to be re-rolled. There is no third option where we repair it.
    """
    import importlib.util as u
    spec = u.spec_from_file_location("mvt", Path(__file__).resolve().parent
                                     / "match-voice-timbre.py")
    mv = u.module_from_spec(spec)
    spec.loader.exec_module(mv)
    m = mv._st()
    R, _ = mv.ltas(m, ref)
    T, _ = mv.ltas(m, tgt)
    if R is None or T is None:
        return None
    return mv.sim(R, T)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episode", required=True, type=Path,
                    help="the episode dir, containing episode.json")
    ap.add_argument("--voice-id", help="defaults to locked/voice-id.txt")
    ap.add_argument("--speed", type=float, default=SPEED)
    ap.add_argument("--rolls", type=int, default=3,
                    help="how many times to re-roll a line that lands outside "
                         "the bar before keeping the closest (default 3)")
    # The clone can sit well below the character's own takes -- measured 123.6 Hz against
    # a 141.0 Hz hook, which is audible as a different person narrating. The model's OWN
    # pitch control fixes it (123.6 -> 139.1 at pitch 1) and, unlike a resample in post,
    # it does not drag the formants with it: a post-hoc shift measured WORSE on spectrum
    # correlation (0.936 -> 0.910). Screen with --pitch, never with asetrate.
    ap.add_argument("--pitch", type=int, default=0,
                    help="MiniMax voice pitch, -12..12. Try 1 before touching anything "
                         "else when the clone reads low against the character")
    ap.add_argument("--target-voice", type=Path,
                    help="defaults to locked/character-voice.mp3")
    ap.add_argument("--check-fit", action="store_true",
                    help="also assert the two lines fit the episode's zoom edit")
    args = ap.parse_args()

    spec_p = args.episode / "episode.json"
    if not spec_p.exists():
        sys.exit("ERROR: no episode.json in %s" % args.episode)
    spec = json.loads(spec_p.read_text(encoding="utf-8"))

    locked = args.episode.parent / "locked"
    vid = args.voice_id or (locked / "voice-id.txt").read_text(encoding="utf-8").strip()
    tgt_p = args.target_voice or (locked / "character-voice.mp3")

    if "FAL_KEY" not in os.environ and "FAL_API_KEY" in os.environ:
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
    if not os.environ.get("FAL_KEY"):
        sys.exit("ERROR: FAL_KEY (or FAL_API_KEY) not set.")
    from fal_client import subscribe

    out_dir = args.episode / "final"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = f0_of(tgt_p)
    print("voice %s  speed %.2f  target %.1f Hz" % (vid, args.speed, target))

    total = 0.0
    bad = []
    # vo_line_c is the v3 results line, spoken over the real output footage. Optional so
    # the old account's four-line episodes still build unchanged.
    wanted = [("vo_line_a", "vo-a"), ("vo_line_b", "vo-b")]
    if spec.get("vo_line_c"):
        wanted.append(("vo_line_c", "vo-c"))
    for key, name in wanted:
        line = spec[key]
        out = out_dir / ("%s.mp3" % name)
        # The clone varies roll to roll: the same line came back 129.0, 130.1 and 132.2 Hz
        # on three passes of the same episode. A line that lands outside the bar is a
        # RE-ROLL, and re-rolling costs cents and touches no picture -- so do it here
        # rather than failing the episode gate fifteen minutes later (#128). Keep the
        # closest roll, not the last one.
        best = None
        for attempt in range(args.rolls):
            r = subscribe(MODEL, arguments={
                "text": line,
                "voice_setting": {"voice_id": vid, "speed": args.speed, "vol": 1.0,
                                  "pitch": args.pitch},
                "output_format": "url",
            }, with_logs=False)
            url = (r.get("audio") or {}).get("url")
            if not url:
                sys.exit("ERROR: no audio returned for %s: %s" % (name, str(r)[:300]))
            cand = out_dir / ("%s-roll%d.mp3" % (name, attempt + 1))
            urllib.request.urlretrieve(url, cand)
            cv, ctb = f0_of(cand), timbre_vs(tgt_p, cand)
            coff = abs((cv or 0) - target) if (cv is not None and target) else 999.0
            good = (ctb is not None and ctb >= TIMBRE_FLOOR and coff <= F0_TOL)
            if best is None or coff < best[0]:
                best = (coff, cand)
            if attempt:
                print("    roll %d: F0 %+.1f Hz, timbre %.3f%s"
                      % (attempt + 1, (cv or 0) - (target or 0),
                         ctb if ctb is not None else -1, "" if good else "  (still off)"))
            if good:
                break
        out.write_bytes(best[1].read_bytes())
        for f in out_dir.glob("%s-roll*.mp3" % name):
            f.unlink(missing_ok=True)
        # SCREEN the take; never repair it. The clone is deterministic and locked, so its
        # output IS the series voice -- shifting it can only move it away (#48).
        v = f0_of(out)
        tb = timbre_vs(tgt_p, out)
        d = dur(out)
        total += d
        # Screen on BOTH properties the verifier gates on. Timbre alone is not enough:
        # verify-episode also asserts F0 within 8 Hz of the locked voice, and the clone
        # varies roll to roll. Part 2's vo-b came back 10.1 Hz flat with a perfectly good
        # timbre of 0.964, was printed "ok" here, and failed the episode gate at the very
        # end of a fifteen minute run (#128). One screen, both questions.
        off = abs((v or 0) - target) if (v is not None and target) else None
        ok = (tb is not None and tb >= TIMBRE_FLOOR
              and off is not None and off <= F0_TOL)
        if not ok:
            why = []
            if tb is None or tb < TIMBRE_FLOOR:
                why.append("timbre %.3f" % (tb if tb is not None else -1))
            if off is None or off > F0_TOL:
                why.append("F0 %+.1f Hz" % ((v or 0) - (target or 0)))
            bad.append("%s (%s)" % (name, ", ".join(why)))
        print("  %-5s %2dw  %5.2fs  %.2f w/s  F0 %.1f (%+.1f)  timbre %.3f  %s"
              % (name, len(line.split()), d, len(line.split()) / max(d, 0.01),
                 v if v is not None else -1, (v or 0) - (target or 0),
                 tb if tb is not None else -1,
                 "ok" if ok else "REJECT -- re-roll this line, do not shift it"))

    if args.check_fit:
        edit = args.episode / "screen-recording" / "zoom-edit.mp4"
        if edit.exists():
            e = dur(edit)
            fits = total <= e - 0.2
            print("\nVO %.2fs vs zoom edit %.2fs -> %s"
                  % (total, e, "fits" if fits else "DOES NOT FIT"))
            if not fits:
                bad.append("fit")
        else:
            print("\n(no zoom-edit.mp4 yet; skipping the fit check)")

    print("\n-> %s" % out_dir)
    if bad:
        print("PROBLEM with: %s" % ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
