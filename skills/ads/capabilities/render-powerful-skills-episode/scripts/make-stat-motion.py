#!/usr/bin/env python3
"""Render the result as a motion-graphic section: numbers that count, not cards that sit.

The payoff went through three treatments before this one. Stills cut to the voice read as
a slideshow; cards overlaid on the terminal were invisible against it; cards overlaid on
the character covered him. This is the fourth: the section is its own black ground, one
figure at a time, and the only thing that moves is the number itself.

Motion rules, deliberately few (the series' taste file: one idea per beat, calm eases,
hard cuts, nothing decorative):
  * the value COUNTS UP over 0.55s on an ease-out, then holds dead still
  * the label masks up 24px and fades in over 0.22s, before the value
  * scenes hard-cut, they never dissolve
  * one accent rule: a lime keyline wipes under the value, 0.35s, then stops
  * nothing glows, nothing bounces, nothing rotates

  make-stat-motion.py --scene "SPENT, LAST 30 DAYS|$|42.3|k|1.9" \\
                      --scene "RETURN ON AD SPEND|      |3.1|x|1.6" --out payoff.mp4

A scene is LABEL|PREFIX|VALUE|SUFFIX|SECONDS. VALUE is what counts up; everything else is
fixed. A non-numeric VALUE simply appears with the label instead of counting.
"""
import argparse, math, shutil, subprocess, sys, tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
BG, FG, MUTED, LIME = (11, 12, 14), (245, 243, 239), (138, 136, 132), (190, 242, 100)
MONO = "C:/Windows/Fonts/consolab.ttf"
SAFE_TOP, SAFE_BOT = 285, 1634


def ease_out(t: float) -> float:
    return 1 - pow(1 - min(max(t, 0.0), 1.0), 3)


def draw_scene(label, prefix, value, suffix, t, dur, val_f, lab_f):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    cy = (SAFE_TOP + SAFE_BOT) // 2

    lab_p = min(t / 0.22, 1.0)                       # label masks up, then the value moves
    if lab_p > 0:
        a = int(255 * ease_out(lab_p))
        dy = int(24 * (1 - ease_out(lab_p)))
        lw = d.textlength(label, font=lab_f)
        d.text(((W - lw) / 2, cy - 190 + dy), label, font=lab_f,
               fill=tuple(int(MUTED[i] * a / 255 + BG[i] * (1 - a / 255)) for i in range(3)))

    try:
        target = float(value)
        # A count-up only reads as one when there is a distance to travel. Counting to
        # "3" spends its first third of a second showing 0, 1, 2 -- so the frame states
        # "0 creatives worn out", the opposite of the claim, right under the label.
        # Small figures appear instead, on the same beat the count would have finished.
        if abs(target) < 10:
            txt = "" if t < 0.12 else prefix + ("%.1f" % target if target % 1
                                                else "%d" % round(target)) + suffix
        else:
            # Start the sweep PART WAY UP. A count from zero puts a lone '0' on the
            # frame right after a hard cut, which is both a weak first frame and, for
            # a moment, a false figure under a true label.
            shown = target * (0.55 + 0.45 * ease_out(max(0.0, (t - 0.12)) / 0.55))
            txt = prefix + ("%.1f" % shown if target % 1 else "%d" % round(shown)) + suffix
    except ValueError:
        txt = prefix + value + suffix
        if t < 0.12:
            txt = ""

    if txt:
        vw = d.textlength(txt, font=val_f)
        d.text(((W - vw) / 2, cy - 90), txt, font=val_f, fill=FG)
        wipe = min(max((t - 0.30) / 0.35, 0.0), 1.0)  # one keyline, one direction, stops
        if wipe > 0:
            x0 = (W - vw) / 2
            d.rectangle([x0, cy + 80, x0 + vw * ease_out(wipe), cy + 88], fill=LIME)
    return im


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", action="append", required=True,
                    metavar="LABEL|PREFIX|VALUE|SUFFIX|SECONDS")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--value-size", type=int, default=190)
    ap.add_argument("--label-size", type=int, default=44)
    a = ap.parse_args()

    val_f = ImageFont.truetype(MONO, a.value_size)
    lab_f = ImageFont.truetype(MONO, a.label_size)
    scenes = []
    for s in a.scene:
        parts = s.split("|")
        if len(parts) != 5:
            sys.exit("a scene is LABEL|PREFIX|VALUE|SUFFIX|SECONDS, got %r" % s)
        scenes.append((parts[0], parts[1], parts[2], parts[3], float(parts[4])))

    work = Path(tempfile.mkdtemp())
    n = 0
    for label, prefix, value, suffix, dur in scenes:
        for f in range(int(round(dur * FPS))):
            draw_scene(label, prefix, value, suffix, f / FPS, dur, val_f, lab_f) \
                .save(work / ("%05d.png" % n))
            n += 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-framerate", str(FPS),
                        "-i", str(work / "%05d.png"), "-c:v", "libx264", "-crf", "15",
                        "-preset", "slow", "-pix_fmt", "yuv420p", str(a.out), "-y"],
                       capture_output=True, text=True)
    shutil.rmtree(work, ignore_errors=True)
    if r.returncode:
        sys.exit(r.stderr[-800:])
    print("[stat-motion] %s  %d frames  %.2fs  %d scenes"
          % (a.out, n, n / FPS, len(scenes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
