#!/usr/bin/env python3
"""Find the DELIVERABLE in a screen recording and pin --notify to it.

WHY THIS EXISTS
--notify is the one number pinned by hand, and it is the cue that has gone wrong most
often: episode 4 was re-pinned twice (once onto the "Brief ready" headline three seconds
before the artefact, once off the finished render into the wrong timeline), and episode 5
shipped 1.35s early because an ad-hoc cyan scan matched the green `Write(...)` line
scrolling through the crop band. Each time the operator had to catch it.

WHAT THE DELIVERABLE IS
The artefact the viewer would click -- a file chip rendered as a filled cyan/teal token,
at the end of a line like "Full matrix saved to <chip>" or "Full brief in <chip>". NOT the
bold headline that announces it, and NOT the tool-call line that wrote it. Both of those
appear seconds earlier and both have been mistaken for it.

HOW IT AVOIDS THE TWO KNOWN TRAPS
  1. Scrolling text drags earlier coloured tokens through any fixed crop, which is what
     produced the 8.20 answer on episode 5. So this scans the WHOLE frame and requires a
     sustained step up in token area -- a token that appears and STAYS.
  2. The deliverable is the LAST such event, not the first. Every episode so far ends its
     demo by naming the file it wrote, and earlier tokens are always the tool calls that
     led there. Validated against the two episodes whose correct answer is known:
     episode 4 -> 10.43 (operator-confirmed), episode 5 -> 9.55 (operator-confirmed).

KNOWN LIMIT -- IT WILL BE WRONG SOMETIMES, SO ALWAYS CHECK THE PROOF FRAMES.
It finds chip-style deliverables reliably (episode 4 -> 10.27 against a confirmed 10.43,
episode 5 -> 9.53 against 9.55) and MISSED episode 6 entirely: that demo ends on a ranked
list and the plain line "Full 30-name list saved." -- no coloured token, and one short line
clears no area threshold. It proposed 5.87 against a correct 7.65. The proof frames caught
it before anything was generated, which is the point: this narrows the search, it does not
decide.

Usage:
    pin-notify.py --edit <ep>/screen-recording/zoom-edit.mp4 --out-dir <ep>/review
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

FPS = 15.0            # enough to place a cue within a frame or two, half the decode cost
MIN_AREA = 90         # px of token at analysis scale; below this it is antialiasing
SUSTAIN = 0.40        # s the token must persist -- scrolling text passes through, a
                      # rendered chip stays put
STEP = 1.10           # a new chip need only ADD to the standing area, not double it: by
                      # the time the deliverable renders the screen is already full of
                      # coloured tool-call tokens, and requiring a doubling missed
                      # episode 4's chip entirely (it answered 2.13 against 10.43)


def text_area(frame: np.ndarray) -> int:
    """Bright terminal text, lower two-thirds. Not every episode ends on a file chip.

    Episode 6's demo finishes on a ranked list and the plain line "Full 30-name list
    saved." -- no coloured token at all -- and the chip detector answered 5.87 (the first
    row of the list) against a correct 7.65. So both signals are reported and the operator
    picks, rather than the tool guessing which shape this episode has.
    """
    h = frame.shape[0]
    s_ = frame[int(h * 0.35):]
    return int(((s_[..., 0] > 150) & (s_[..., 1] > 150) & (s_[..., 2] > 150)).sum())


def token_area(frame: np.ndarray) -> int:
    """Filled cyan/teal token pixels: green and blue high, red clearly lower."""
    r, g, b = frame[..., 0].astype(int), frame[..., 1].astype(int), frame[..., 2].astype(int)
    return int(((g > 110) & (b > 110) & (r < g - 30)).sum())


def series(edit: Path, width: int = 640):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(edit), "-vf",
                        "fps=%.3f,scale=%d:-2" % (FPS, width), "-pix_fmt", "rgb24",
                        "-f", "rawvideo", "-"], capture_output=True)
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    if not len(buf):
        return None, None
    h = None
    for cand in range(int(width * 1.2), int(width * 2.6)):
        if len(buf) % (width * cand * 3) == 0:
            h = cand
            break
    if h is None:
        return None, None
    n = len(buf) // (width * h * 3)
    F = buf[:n * width * h * 3].reshape(n, h, width, 3)
    return (np.array([token_area(F[i]) for i in range(n)]),
            np.array([text_area(F[i]) for i in range(n)]))


def find_deliverable(area: np.ndarray, min_area: int = MIN_AREA):
    """Last sustained step up in area."""
    hold = max(1, int(SUSTAIN * FPS))
    events = []
    for i in range(1, len(area) - hold):
        prev = float(np.median(area[max(0, i - hold):i])) if i else 0.0
        now = float(np.median(area[i:i + hold]))
        if now - prev >= min_area and now >= max(prev * STEP, min_area):
            if not events or (i / FPS) - events[-1][0] > SUSTAIN:
                events.append((i / FPS, prev, now))
    return events


def refine(edit: Path, t: float, half: float) -> float:
    """Re-scan a +/-0.5s window at full frame rate for the exact crossing."""
    lo = max(0.0, t - 0.50)
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(edit), "-ss", "%.3f" % lo,
                        "-t", "1.10", "-vf", "scale=640:-2", "-pix_fmt", "rgb24",
                        "-f", "rawvideo", "-"], capture_output=True)
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    if not len(buf):
        return t
    h = None
    for cand in range(768, 1664):
        if len(buf) % (640 * cand * 3) == 0:
            h = cand
            break
    if h is None:
        return t
    n = len(buf) // (640 * h * 3)
    if n < 2:
        return t
    F = buf[:n * 640 * h * 3].reshape(n, h, 640, 3)
    fps = n / 1.10
    for i in range(n):
        if token_area(F[i]) >= half:
            return round(lo + i / fps, 2)
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edit", required=True, type=Path)
    ap.add_argument("--out-dir", type=Path)
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg not on PATH.")
    if not args.edit.exists():
        sys.exit("ERROR: no such edit: %s" % args.edit)

    area, text = series(args.edit)
    if area is None:
        sys.exit("ERROR: could not decode %s" % args.edit)
    events = find_deliverable(area)
    tevents = find_deliverable(text, min_area=120)

    print("CANDIDATES -- confirm with the proof frames, do not trust this blind:\n")
    if events:
        print("  file chip (a cyan token that appears and stays):")
        for t, a, b in events[-3:]:
            print("     %6.2fs   %d -> %d%s"
                  % (t, int(a), int(b), "   <-- last" if (t, a, b) == events[-1] else ""))
    if tevents:
        print("  result text (the last line of output to render):")
        for t, a, b in tevents[-3:]:
            print("     %6.2fs   %d -> %d%s"
                  % (t, int(a), int(b), "   <-- last" if (t, a, b) == tevents[-1] else ""))
    if not events and not tevents:
        print("nothing found -- pin this one by eye", file=sys.stderr)
        return 1
    if not events:
        events = tevents
    t = events[-1][0]
    print("\n--notify %.2f      (edit-relative; the finished video adds the hook length)" % t)

    out = args.out_dir or args.edit.parent
    out.mkdir(parents=True, exist_ok=True)
    for tag, when in (("before", max(0.0, t - 0.30)), ("at", t + 0.10)):
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(args.edit), "-ss", "%.3f" % when,
                        "-frames:v", "1", "-vf", "scale=460:-2", "-update", "1",
                        str(out / ("cue-%s.png" % tag)), "-y"], capture_output=True)
    print("proof frames -> %s/cue-before.png and cue-at.png. LOOK AT THEM: the chip must be"
          % out)
    print("absent in the first and present in the second.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
