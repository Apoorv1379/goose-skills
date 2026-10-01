#!/usr/bin/env python3
"""Cut a character take on its BEST SETTLED frame, not on the first sign of drift.

`trim-tail.py` cuts where the picture starts moving on from the speech, which protects
against the drifting tail (#45) but says nothing about what the last frame LOOKS like. The
operator's complaint -- "he's looking off camera in between", "he's fumbling" -- is about
exactly that: our takes ended mid-blink or mid-word while the shipped episodes end settled,
facing the lens.

Measured on a shipped episode, last frame against a mid-line reference:

    episode 8 hook    0.906        our first pass   0.858-0.885
    episode 8 closer  0.924

So this scans every candidate end from the line's end to the take's end, scores each one
the way `verify-episode.py` scores the last frame, and cuts at the best. Drift still bounds
the search: a settled frame 2s into a warping tail is not settled, it is a different shot.

  trim-settle.py raw.mp4 --after 3.4 --out final/hook.mp4

--after is where the spoken line ends (trim-tail prints it). Nothing before that is
considered, so the line is never clipped.
"""
import argparse, subprocess, sys
from pathlib import Path

import numpy as np
from PIL import Image


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit((r.stderr or r.stdout)[-1200:])
    return r


def dur(p: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", p]).stdout.strip())


def grab(clip: Path, t: float, tmp: Path) -> np.ndarray:
    sh(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", clip, "-frames:v", "1", tmp, "-y"])
    return np.asarray(Image.open(tmp).convert("L").resize((240, 426))).astype(float)


def corr(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", type=Path)
    ap.add_argument("--after", type=float, required=True,
                    help="seconds; the spoken line's end, from trim-tail")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--pad", type=float, default=0.25,
                    help="minimum gap kept after the last word")
    ap.add_argument("--step", type=float, default=0.06)
    a = ap.parse_args()

    total = dur(a.clip)
    tmp = a.out.parent / "_settle.png"
    a.out.parent.mkdir(parents=True, exist_ok=True)
    ref = grab(a.clip, a.after * 0.5, tmp)          # mid-line: he is talking to the lens

    lo, hi = a.after + a.pad, total - 0.05
    if hi <= lo:
        sys.exit("ERROR: nothing after the line to choose from (%.2fs line, %.2fs take)"
                 % (a.after, total))

    best, scores = None, []
    t = lo
    while t <= hi:
        s = corr(ref, grab(a.clip, t, tmp))
        scores.append((s, t))
        if best is None or s > best[0]:
            best = (s, t)
        t += a.step
    tmp.unlink(missing_ok=True)

    # A late frame that scores well inside a drifting tail is suspect, so prefer the
    # EARLIEST time within 0.01 of the best -- same settle, less tail to go wrong.
    cut = min(t for s, t in scores if s >= best[0] - 0.01)
    sh(["ffmpeg", "-v", "error", "-i", a.clip, "-t", "%.3f" % cut,
        "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-c:a", "aac", "-b:a", "192k",
        a.out, "-y"])
    print("[settle] %-22s line ends %.2fs, cut %.2fs, settle %.3f (was %.3f at the floor)"
          % (a.clip.name, a.after, cut, best[0], scores[0][0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
