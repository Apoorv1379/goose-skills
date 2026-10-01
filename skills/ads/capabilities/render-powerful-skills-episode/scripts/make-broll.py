#!/usr/bin/env python3
"""Cut real output stills into a b-roll segment: cold open or results.

The format's payoff used to be the terminal's own last frame. From v3 the payoff is the
work itself -- real finished creative, full frame, hard cuts, no motion. Stills only:
every push-in tried on these read as a slideshow, and the taste rule for the series is
motion earns its place or it does not happen.

  make-broll.py --out cold-open.mp4 --shot a.jpg:0.7 --shot b.jpg:0.7 --shot c.jpg:0.7

A shot is PATH:SECONDS. Anything taller than the frame is cropped to fill; anything
squarer is fitted inside the caption safe zone (y 285-1634) and centred on black, so a
1:1 ad never collides with the caption band underneath it.
"""
import argparse, subprocess, sys, tempfile
from pathlib import Path

W, H, FPS = 1080, 1920, 30
SAFE_TOP, SAFE_BOT = 285, 1634          # the 4:5 social safe zone, same as make-caption.py
SAFE_H = SAFE_BOT - SAFE_TOP


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit("ffmpeg failed:\n" + (r.stderr or "")[-1500:])
    return r


def probe(p: Path) -> tuple[int, int]:
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", p])
    w, h = r.stdout.strip().split("x")
    return int(w), int(h)


def shot(src: Path, seconds: float, out: Path):
    """One still, one cut. Fill the frame when the image is tall enough to survive it."""
    w, h = probe(src)
    if h / w >= H / W * 0.92:
        # tall enough to fill: scale to cover, then crop the frame out of the middle
        vf = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
              % (W, H, W, H))
    else:
        # square or landscape: fit inside the safe zone so the caption band stays clear
        vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
              "pad=%d:%d:(ow-iw)/2:%d:black"
              % (W, SAFE_H, W, H, SAFE_TOP))
    sh(["ffmpeg", "-v", "error", "-loop", "1", "-t", "%.3f" % seconds, "-i", src,
        "-vf", vf + ",fps=%d,setsar=1,format=yuv420p" % FPS,
        "-c:v", "libx264", "-crf", "16", "-preset", "medium", out, "-y"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", action="append", required=True,
                    metavar="PATH:SECONDS", help="repeatable, in order")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()

    shots = []
    for s in a.shot:
        path, _, secs = s.rpartition(":")
        p = Path(path)
        if not p.exists():
            sys.exit("no such still: %s" % p)
        shots.append((p, float(secs)))

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        parts = []
        for i, (p, secs) in enumerate(shots):
            part = work / ("%02d.mp4" % i)
            shot(p, secs, part)
            parts.append(part)

        lst = work / "concat.txt"
        lst.write_text("".join("file '%s'\n" % p.resolve().as_posix() for p in parts),
                       encoding="utf-8")
        a.out.parent.mkdir(parents=True, exist_ok=True)
        sh(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
            "-c", "copy", a.out, "-y"])

    total = sum(s for _, s in shots)
    print("[broll] %s  %.2fs  %d shots" % (a.out, total, len(shots)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
