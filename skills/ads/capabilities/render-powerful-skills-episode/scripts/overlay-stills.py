#!/usr/bin/env python3
"""Cut real output stills INTO the screen recording, at given times.

Two ways to show what a skill produced. `make-broll.py` builds a standalone segment,
which suits a run whose payoff is a set of finished images. This one drops a still over
the terminal while it is still running, which suits a run that names its output long
before it finishes -- the viewer sees the thing being described as it is described.

  overlay-stills.py --base zoom-edit.mp4 --out overlaid.mp4 \
      --cut 6.5:ad-1.jpg:1.6 --cut 9.0:ad-2.jpg:1.6

A cut is AT:PATH:SECONDS, in base-clip time. Full frame and hard in/out by default: an
inset card over a terminal reads as a compositing trick and the series rule is to show
real UI full frame or not at all. --inset draws it as a card instead, for the cases
where the terminal underneath is the point and the still is the aside.

The base clip's own length never changes -- a cut covers the footage under it rather
than pushing it later, so `--notify` and every downstream timing mark stay valid.
"""
import argparse, subprocess, sys, tempfile
from pathlib import Path

W, H, FPS = 1080, 1920, 30
SAFE_TOP, SAFE_BOT = 285, 1634
INSET_W, INSET_Y = 760, 620          # card width and top edge, inside the safe zone


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit("ffmpeg failed:\n" + (r.stderr or "")[-1500:])
    return r


def dur(p: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", p]).stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cut", action="append", required=True, metavar="AT:PATH:SECONDS")
    ap.add_argument("--inset", action="store_true",
                    help="draw as a card over the terminal instead of full frame")
    a = ap.parse_args()

    cuts = []
    for c in a.cut:
        at, path, secs = c.split(":")[0], ":".join(c.split(":")[1:-1]), c.split(":")[-1]
        p = Path(path)
        if not p.exists():
            sys.exit("no such still: %s" % p)
        cuts.append((float(at), p, float(secs)))
    cuts.sort()

    base_len = dur(a.base)
    for at, p, secs in cuts:
        if at + secs > base_len + 1e-3:
            sys.exit("cut at %.2fs + %.2fs runs past the base clip (%.2fs)"
                     % (at, secs, base_len))
    for (a1, _, d1), (a2, _, _) in zip(cuts, cuts[1:]):
        if a1 + d1 > a2 + 1e-3:
            sys.exit("cuts overlap at %.2fs" % a2)

    inputs, filters, overlaid = ["-i", str(a.base)], [], "0:v"
    for i, (at, p, secs) in enumerate(cuts, start=1):
        inputs += ["-loop", "1", "-t", "%.3f" % secs, "-i", str(p)]
        if a.inset:
            scale = ("scale=%d:-1,pad=%d:ih+24:12:12:black@0.9" % (INSET_W - 24, INSET_W))
            pos = "(W-w)/2:%d" % INSET_Y
        else:
            scale = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                     "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black"
                     % (W, SAFE_BOT - SAFE_TOP, W, H))
            pos = "0:0"
        filters.append("[%d:v]%s,fps=%d,setsar=1[s%d]" % (i, scale, FPS, i))
        filters.append("[%s][s%d]overlay=%s:enable='between(t,%.3f,%.3f)'[o%d]"
                       % (overlaid, i, pos, at, at + secs, i))
        overlaid = "o%d" % i

    a.out.parent.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-v", "error", *inputs, "-filter_complex", ";".join(filters),
        "-map", "[%s]" % overlaid, "-map", "0:a?", "-c:v", "libx264", "-crf", "16",
        "-preset", "medium", "-c:a", "copy", a.out, "-y"])

    out_len = dur(a.out)
    print("[overlay] %s  %.2fs  %d cuts  (base %.2fs, unchanged: %s)"
          % (a.out, out_len, len(cuts), base_len, abs(out_len - base_len) < 0.05))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
