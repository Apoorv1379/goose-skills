#!/usr/bin/env python3
"""Assemble a finished episode from locked clips, VO, and a zoom edit.

    hook (captioned)  ->  zoom edit under VO A + VO B, with SFX  ->  closer

This is the pipeline episodes 1 and 2 actually shipped on. An earlier version of this
script cut its own beats out of a landscape screen recording and burned captions in with
ffmpeg `drawtext`; both episodes ended up doing neither, and the old defaults reproduced
caption treatments that were explicitly rejected. If you ever do need to cut fresh beats
from a raw capture, that is a separate job -- do it first and pass the result as
--zoom-edit.

Everything below is measured, not assumed:

  * SFX come from build-sfx.py: whoosh times from motion detection in the edit, whoosh
    gain from the VO level in each whoosh's own window, scan from the settle of the last
    zoom to the notification.
  * The caption comes from make-caption.py, which reproduces episode 1 pixel-for-pixel.
  * Section loudness is matched to the last shipped episode with two-pass loudnorm in
    linear mode. A plain `volume=` nudge lands harder than asked when a limiter is already
    working -- that is critical knowledge #34.

The ONE number you must pin by hand is --notify: the moment the result appears on screen.
Step frames through the edit to find it. It is not the tool-call line; in episode 1 those
were 4.5 seconds apart (critical knowledge #11).

Usage:
    build-episode.py --part 2 \\
        --hook   <ep>/final/hook-tuned.mp4 \\
        --closer <ep>/final/closer.mp4 \\
        --vo-a   <ep>/final/vo-a.mp3  --vo-b <ep>/final/vo-b.mp3 \\
        --zoom-edit <ep>/screen-recording/zoom-edit.mp4 \\
        --notify 11.15 \\
        --out <ep>/output/episode-2-final.mp4

SFX come from the skill's own assets/sfx/ by default; --sfx-dir overrides.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

W, H, FPS = 1080, 1920, 30

# Episode 1's per-section integrated loudness, the series reference (critical knowledge #34).
TARGET_I = {"hook": -19.86, "middle": -17.52, "closer": -16.37}
TARGET_I["results"] = TARGET_I["middle"]   # v3: same bed as the middle, same voice
TARGET_TP = -1.5

HERE = Path(__file__).resolve().parent
DUCK_GAIN = 0.35   # -9.1 dB dip in the VO under each whoosh. Deeper than it looks: the
                   # middle is loudness-normalised AFTER the mix, which pushes the whole
                   # section back up and absorbed most of a -5 dB duck (net 1.5 dB
                   # measured). Set the duck for the NET you want, not the nominal (#57).


def sh(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          errors="replace", **kw)


def need(cmd, label):
    r = sh(cmd)
    if r.returncode != 0:
        print("ERROR: %s failed:\n%s" % (label, (r.stderr or r.stdout)[-1500:]), file=sys.stderr)
        sys.exit(1)
    return r


# How long the deliverable must stay on screen after it appears. The camera pass
# already trims the plate to deliverable + 1.2s, so this only has to survive the cut.
DELIVERABLE_HOLD = 1.0


def dur(p: Path) -> float:
    out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", p]).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def loudness(p: Path) -> tuple[float, float]:
    r = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", p,
            "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    txt = r.stderr + r.stdout
    i = re.search(r'"input_i"\s*:\s*"(-?[\d.]+)"', txt)
    tp = re.search(r'"input_tp"\s*:\s*"(-?[\d.]+)"', txt)
    return (float(i.group(1)) if i else 0.0, float(tp.group(1)) if tp else 0.0)


def loudnorm_to(src: Path, out: Path, target_i: float):
    """Two-pass loudnorm in LINEAR mode -- a straight gain, so the mix is not reshaped."""
    r = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", src,
            "-af", "loudnorm=I=%s:TP=%s:LRA=11:print_format=json" % (target_i, TARGET_TP),
            "-f", "null", "-"])
    txt = r.stderr + r.stdout
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", txt, re.S)
    if not m:
        sys.exit("ERROR: loudnorm measurement pass produced no JSON for %s" % src)
    d = json.loads(m.group(0))
    need(["ffmpeg", "-v", "error", "-i", src, "-af",
          "loudnorm=I=%s:TP=%s:LRA=11:measured_I=%s:measured_TP=%s:measured_LRA=%s:"
          "measured_thresh=%s:linear=true"
          % (target_i, TARGET_TP, d["input_i"], d["input_tp"], d["input_lra"],
             d["input_thresh"]),
          "-ar", "48000", out, "-y"], "loudnorm apply")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for a in ("hook", "closer", "vo-a", "vo-b", "zoom-edit", "out"):
        ap.add_argument("--" + a, required=True, type=Path)
    # v3: the results section. Optional, so the old account's four-line episodes build
    # unchanged. --results is the silent b-roll from pin-broll.py, already cut to vo-c.
    ap.add_argument("--results", type=Path,
                    help="v3 results b-roll, cut to --vo-c by pin-broll.py")
    ap.add_argument("--vo-c", type=Path, help="v3 results voiceover")
    ap.add_argument("--sfx-dir", type=Path, default=HERE.parent / "assets" / "sfx",
                    help="defaults to the skill's canonical assets/sfx/")
    ap.add_argument("--part", type=int, required=True)
    ap.add_argument("--notify", type=float, required=True,
                    help="the moment the RESULT appears on screen, in edit time, pinned by "
                         "stepping frames -- NOT the tool-call line (critical knowledge #11)")
    ap.add_argument("--whoosh-times", help="comma-separated, overrides motion detection")
    ap.add_argument("--hold", type=float, default=0.35,
                    help="seconds the middle holds after the last VO word before cutting "
                         "to the closer. Episode 2 shipped 0.34s")
    ap.add_argument("--dur-range", help="MIN:MAX seconds, passed to verify-episode.py. "
                    "v3 episodes with a results section use 32:56")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg not on PATH.")
    for a in ("hook", "closer", "vo_a", "vo_b", "zoom_edit", "sfx_dir"):
        p = getattr(args, a)
        if not p.exists():
            sys.exit("ERROR: missing %s: %s" % (a, p))

    work = args.out.parent / "_build"
    work.mkdir(parents=True, exist_ok=True)

    if bool(args.results) != bool(args.vo_c):
        sys.exit("ERROR: --results and --vo-c go together, or neither.")
    if args.results and not args.results.exists():
        sys.exit("ERROR: missing results: %s" % args.results)
    if args.vo_c and not args.vo_c.exists():
        sys.exit("ERROR: missing vo_c: %s" % args.vo_c)

    edit_len = dur(args.zoom_edit)
    vo_a, vo_b = dur(args.vo_a), dur(args.vo_b)
    print("hook %.2fs | edit %.2fs | VO %.2f + %.2f = %.2fs | closer %.2fs"
          % (dur(args.hook), edit_len, vo_a, vo_b, vo_a + vo_b, dur(args.closer)))
    # If the episode will land short, say WHICH lever to pull. Both are available and only
    # one is safe: a VO re-roll costs cents and touches no picture, while extending a clip
    # walks into the accumulating push-in. Episode 10 was extended for exactly this reason
    # and shipped a visibly zoomed hook that the operator caught (#70, #71).
    # The middle must never end before the deliverable is on screen (#82).
    floor = args.notify + DELIVERABLE_HOLD
    est = dur(args.hook) + min(edit_len, max(vo_a + vo_b + args.hold, floor)) + dur(args.closer)
    if est < 20.0:
        print("  WARNING: this will come to about %.2fs, under the 20s floor.\n"
              "  RE-ROLL THE VOICEOVER, do not extend the clips -- the VO is a few cents\n"
              "  and touches no picture; a longer clip walks into the push-in (#70, #71).\n"
              "  The middle caps at the edit length (%.2fs), so check the VO can even\n"
              "  cover the gap before rolling it." % (est, edit_len))
    if vo_a + vo_b > edit_len + 0.05:
        sys.exit("ERROR: VO (%.2fs) is longer than the zoom edit (%.2fs). Re-time the VO "
                 "or extend the edit; do not stretch either to fit."
                 % (vo_a + vo_b, edit_len))

    # The middle runs for the VO plus a short hold, NOT for the whole edit. Using the full
    # edit leaves dead air: episode 3's first cut ran 1.66s past the last word with the SFX
    # bed already finished, which reads as the episode stalling before the closer.
    # Episode 2's shipped middle held 0.34s after its VO, so that is the model.
    middle_len = min(edit_len, max(vo_a + vo_b + args.hold, floor))
    if floor > vo_a + vo_b + args.hold:
        print("  holding the middle to %.2fs so the deliverable at %.2fs stays on screen"
              % (middle_len, args.notify))
        print("  -- the VO alone would have cut it %.2fs early (#82)."
              % (args.notify - (vo_a + vo_b + args.hold)))
    if edit_len - middle_len > 0.05:
        print("  trimming the edit %.2fs -> %.2fs so it ends %.2fs after the last word"
              % (edit_len, middle_len, args.hold))
    else:
        print("  VO ends %.2fs before the cut to the closer." % (edit_len - vo_a - vo_b))

    # --- VO: concatenate and pad to the edit's length -------------------------------
    need(["ffmpeg", "-v", "error", "-i", args.vo_a, "-i", args.vo_b, "-filter_complex",
          "[0:a][1:a]concat=n=2:v=0:a=1,aformat=sample_fmts=fltp:sample_rates=48000:"
          "channel_layouts=stereo,apad,atrim=0:%.3f[a]" % middle_len,
          "-map", "[a]", work / "vo.wav", "-y"], "VO concat")

    # --- SFX bed --------------------------------------------------------------------
    cmd = [sys.executable, HERE / "build-sfx.py", "--edit", args.zoom_edit,
           "--vo", work / "vo.wav", "--sfx-dir", args.sfx_dir,
           "--notify", args.notify, "--out", work / "sfx.wav"]
    if args.whoosh_times:
        cmd += ["--whoosh-times", args.whoosh_times]
    r = need(cmd, "build-sfx.py")
    print(r.stdout.rstrip())
    # build-sfx works in the EDIT's timeline, so its log prints a number that looks like a
    # position in the finished video and is not one -- the middle starts after the hook.
    # Episode 4 was pinned off the render at 12.20 and the chime landed at 16.95 (#46).
    print("  -> in the FINISHED video that chime is at %.2fs (edit %.2f + hook %.2f). "
          "Scrub the render THERE." % (args.notify + dur(args.hook), args.notify,
                                       dur(args.hook)))

    # --- Middle audio: VO + SFX, then matched to the reference episode ---------------
    # Duck the VOICEOVER under each whoosh so the hit reads. The operator reported the
    # whooshes as inaudible on two episodes while they measured present the whole time --
    # at equal level the ear merges them into the voice (#55). Measured in episode 5 the
    # whoosh sat at 0.41x the voice, so it loses; winning by level alone needs ~8 dB more,
    # which is harsh. A short dip in the voice wins it back cheaply (#57).
    duck = ""
    wt_file = work / "whoosh-times.json"
    if wt_file.exists():
        for t_ in json.loads(wt_file.read_text(encoding="utf-8")):
            duck += ("volume=enable='between(t,%.2f,%.2f)':volume=%.3f," %
                     (max(0.0, t_ - 0.05), t_ + 0.35, DUCK_GAIN))
    if duck:
        fc = ("[0:a]%s[v];[v][1:a]amix=inputs=2:duration=first:normalize=0[a]"
              % duck.rstrip(","))
        print("  ducking the VO %.1f dB under %d whooshes"
              % (20 * math.log10(DUCK_GAIN), duck.count("volume=enable")))
    else:
        fc = "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[a]"
    need(["ffmpeg", "-v", "error", "-i", work / "vo.wav", "-i", work / "sfx.wav",
          "-filter_complex", fc,
          "-map", "[a]", work / "mid_raw.wav", "-y"], "middle mix")
    loudnorm_to(work / "mid_raw.wav", work / "mid.wav", TARGET_I["middle"])

    if args.results:
        # The b-roll is already cut to the line, so the section length comes from the
        # PICTURE here, not from the voice: pin-broll gave the last still its hold.
        res_len = dur(args.results)
        need(["ffmpeg", "-v", "error", "-i", args.vo_c, "-af",
              "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
              "apad,atrim=0:%.3f" % res_len, work / "res_raw.wav", "-y"], "VO C pad")
        loudnorm_to(work / "res_raw.wav", work / "res.wav", TARGET_I["middle"])

    need(["ffmpeg", "-v", "error", "-i", args.closer, "-vn", "-c:a", "pcm_s16le",
          work / "clo_raw.wav", "-y"], "closer audio extract")
    loudnorm_to(work / "clo_raw.wav", work / "clo.wav", TARGET_I["closer"])

    need(["ffmpeg", "-v", "error", "-i", args.hook, "-vn", "-c:a", "pcm_s16le",
          work / "hk_raw.wav", "-y"], "hook audio extract")
    loudnorm_to(work / "hk_raw.wav", work / "hk.wav", TARGET_I["hook"])

    # --- Caption ---------------------------------------------------------------------
    cap_spec = {}
    ep_spec = args.hook.parent.parent / "episode.json"
    if ep_spec.exists():
        cap_spec = json.loads(ep_spec.read_text(encoding="utf-8"))
    cap_args = []
    for k, flag in (("caption_line1", "--line1"), ("caption_line2", "--line2"),
                    ("caption_box_top", "--box-top")):
        if cap_spec.get(k):
            cap_args += [flag, str(cap_spec[k])]
    r = need([sys.executable, HERE / "make-caption.py", "--part", args.part,
              "--out", work / "cap.png"] + cap_args, "make-caption.py")
    print(r.stdout.rstrip())

    # --- Sections ---------------------------------------------------------------------
    need(["ffmpeg", "-v", "error", "-i", args.hook, "-i", work / "cap.png",
          "-i", work / "hk.wav", "-filter_complex",
          "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,"
          "fps=%d[b];[b][1:v]overlay=0:0:format=auto[v]" % (W, H, W, H, FPS),
          "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "17", "-preset", "slow",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          work / "hook.mp4", "-y"], "hook caption pass")

    need(["ffmpeg", "-v", "error", "-i", args.zoom_edit, "-i", work / "mid.wav",
          "-t", "%.3f" % middle_len,
          "-filter_complex", "[0:v]fps=%d,scale=%d:%d,setsar=1[v]" % (FPS, W, H),
          "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "17", "-preset", "slow",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          work / "middle.mp4", "-y"], "middle build")

    # The closer routinely arrives at the generator's native size and rate, not the
    # episode's -- episode 2's came back 720x1280/24.
    need(["ffmpeg", "-v", "error", "-i", args.closer, "-i", work / "clo.wav",
          "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,setsar=1,"
                 "fps=%d" % (W, H, W, H, FPS),
          "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "17", "-preset", "slow",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
          work / "closer.mp4", "-y"], "closer conform")

    if args.results:
        need(["ffmpeg", "-v", "error", "-i", args.results, "-i", work / "res.wav",
              "-filter_complex", "[0:v]fps=%d,scale=%d:%d:force_original_aspect_ratio="
              "increase,crop=%d:%d,setsar=1[v]" % (FPS, W, H, W, H),
              "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "17",
              "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
              "-ar", "48000", work / "results.mp4", "-y"], "results build")

    lst = work / "concat.txt"
    # ABSOLUTE paths. The concat demuxer resolves relative entries against the LIST FILE's
    # own directory, so a relative --out silently produces a doubled path and "No such file
    # or directory". It only worked before because --out happened to be absolute.
    lst.write_text("".join("file '%s'\n" % (work / n).resolve().as_posix()
                           for n in (("hook.mp4", "middle.mp4", "results.mp4",
                                      "closer.mp4") if args.results else
                                     ("hook.mp4", "middle.mp4", "closer.mp4"))),
                   encoding="utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    need(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
          "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
          args.out, "-y"], "final concat")

    total = dur(args.out)
    fi, ftp = loudness(args.out)
    print("\nsection loudness:")
    for name in (("hook", "middle", "results", "closer") if args.results
                 else ("hook", "middle", "closer")):
        i, tp = loudness(work / ("%s.mp4" % name))
        print("  %-7s %7.2f LUFS  (target %.2f, delta %+.2f)   TP %.2f"
              % (name, i, TARGET_I[name], i - TARGET_I[name], tp))
    print("  %-7s %7.2f LUFS   TP %.2f   (episode 1: -17.32 / -1.47)" % ("FINAL", fi, ftp))
    print("\n-> %s  (%.2fs)" % (args.out, total))
    if not 20.0 <= total <= 26.0:
        print("WARNING: %.2fs is outside the 20-26s target." % total, file=sys.stderr)

    # Run the format's own checks. These are properties of the SERIES, not of one episode,
    # so they are asserted here rather than left to whoever happens to be reviewing.
    ep_dir = args.hook.parent.parent
    if (ep_dir / "episode.json").exists():
        print()
        v = sh([sys.executable, HERE / "verify-episode.py",
                "--episode", ep_dir, "--final", args.out]
               + (["--dur-range", args.dur_range] if args.dur_range else []))
        print((v.stdout or v.stderr).rstrip())
        if v.returncode != 0:
            print("\nThe file was written, but it does NOT meet the format. Fix the "
                  "failures above before shipping it.", file=sys.stderr)

    print("\nStill to check BY HAND -- the checks above cannot:")
    print("  * watch it end to end for eye distortion at the two cuts")
    print("  * confirm the notification landed on the result frame, in THIS file")
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
