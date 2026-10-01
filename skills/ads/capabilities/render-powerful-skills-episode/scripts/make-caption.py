#!/usr/bin/env python3
"""Render the hook caption as a 1080x1920 RGBA overlay PNG (State 9).

Every number here is locked by SKILL.md State 9 and was arrived at by measurement, not
taste. The two that cost the most to find:

  * ONE rounded box behind BOTH lines. ffmpeg's drawtext box=1 draws a separate
    square-cornered rectangle per call, which is wrong on both counts -- hence a PNG.
  * Letter TRACKING. PIL applies no kerning at all, and in Helvetica Bold `r` has almost
    no right bearing while `f` leans left, so "Powerful" reads visibly crowded at default
    spacing. Hence the per-pair nudges in PAIR_TRACK.

Position is the other trap: the 4:5 social safe zone on a 1080x1920 frame is y=285..1634,
and a box that is technically inside by 19px still reads as outside. BOX_TOP=345 leaves a
60px margin. Verify in the finished render by detecting the band colour, never by eye.

Usage:
    make-caption.py --part 2 --out cap-ep2.png
    ffmpeg -i hook.mp4 -i cap-ep2.png -filter_complex "[0:v][1:v]overlay=0:0:format=auto" ...
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920

FONT = "C:/Users/apoor/AppData/Local/Microsoft/Windows/Fonts/Helvetica-Bold.ttf"
SIZE = 76
FG = (255, 255, 255, 255)
BG = (199, 209, 143, 255)          # #c7d18f

RADIUS = 30
PAD_X = 34
PAD_TOP = 14
PAD_BOT = 18
LEADING = 8
BOX_TOP = 345                      # safe zone is y=285..1634; this leaves 60px

TRACK = 1.5                        # uniform, every pair
PAIR_TRACK = {"rf": 3.5, "rt": 2.5}

LINE1 = "Powerful Claude Skills"
LINE2 = "You Should Know! Pt. %d"


def track_for(a: str, b: str) -> float:
    return TRACK + PAIR_TRACK.get(a + b, 0.0)


def line_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    w = 0.0
    for i, ch in enumerate(text):
        w += font.getlength(ch)
        if i + 1 < len(text):
            w += track_for(ch, text[i + 1])
    return w


def draw_tracked(d: ImageDraw.ImageDraw, xy, font, text: str):
    x, y = xy
    for i, ch in enumerate(text):
        d.text((x, y), ch, font=font, fill=FG)
        x += font.getlength(ch)
        if i + 1 < len(text):
            x += track_for(ch, text[i + 1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", type=int, required=True)
    # The series title is per-SHOW, not per-episode: @gooseaitools runs "Powerful Skills
    # for Claude" with no part number on screen, because the episode number is spoken and
    # counted on the fingers instead. Defaults keep the original series byte-identical.
    ap.add_argument("--box-top", type=int, default=None,
                    help="y of the caption box top. Default is the original series' %d, "
                         "which assumes empty wall above the head; a character framed "
                         "higher needs the box moved or it lands on his forehead" % BOX_TOP)
    ap.add_argument("--line1", default=LINE1)
    ap.add_argument("--line2", default=LINE2,
                    help="may contain %%d for the part number; omit it for no number")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--font", default=FONT)
    args = ap.parse_args()

    font = ImageFont.truetype(args.font, SIZE)
    ascent, descent = font.getmetrics()
    lh = ascent + descent

    lines = [args.line1,
             (args.line2 % args.part) if "%d" in args.line2 else args.line2]
    widths = [line_width(font, t) for t in lines]

    box_top = args.box_top if args.box_top is not None else BOX_TOP
    box_w = int(round(max(widths))) + 2 * PAD_X
    box_h = lh * 2 + LEADING + PAD_TOP + PAD_BOT
    box_x = (W - box_w) // 2

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([box_x, box_top, box_x + box_w, box_top + box_h],
                        radius=RADIUS, fill=BG)

    y = box_top + PAD_TOP
    for t, w in zip(lines, widths):
        draw_tracked(d, ((W - w) / 2, y), font, t)
        y += lh + LEADING

    args.out.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.out)
    print("box %dx%d at (%d,%d)  bottom y=%d  (safe zone 285..1634)"
          % (box_w, box_h, box_x, box_top, box_top + box_h))
    print("-> %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
