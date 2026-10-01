#!/usr/bin/env python3
"""Gate an outfit-variant reference image before it is ever used for a paid generation.

WHY THIS EXISTS
The character is locked to two reference images and must never be regenerated from text
(critical knowledge #0) -- identity breaks immediately if you do. But ten episodes in one
grey henley reads as a single session chopped up, which undercuts the "recorded in his
room" premise the whole format rests on. The resolution is an IMAGE EDIT that changes the
garment and nothing else.

"Nothing else" is checkable, and that is the point of this script. A correct outfit edit
leaves the face pixel-identical and the room pixel-identical, and changes the torso. Three
measurements, run before spending anything:

    face   unchanged   -- identity held, and the skin pores that make him read as real
                          were not smoothed away (#20)
    room   unchanged   -- same desk, same monitor content, same blinds, same light
    torso  CHANGED     -- the edit actually did the thing

A variant that fails any of these is rejected. It costs nothing to re-edit and a full
episode to discover the face moved after generating.

VERIFY THE BOXES, ALWAYS
This script writes a debug overlay every run and you must look at it. A crop box that
lands somewhere unintended still produces confident numbers -- an eye-detection crop on
this series once sat on the hairline and reported zero blinks in clips that plainly had
them. The default boxes are fractions tuned to the two locked references; a different
pose needs --face/--torso/--room.

Usage:
    check-outfit.py --locked locked/character-selfie.png \\
                    --variant outfits/ep03/character-selfie.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# x0, y0, x1, y1 as fractions of the image. Tuned to the two locked references, which
# frame him the same way; check the overlay if you change pose or crop.
FACE = (0.46, 0.28, 0.80, 0.56)
# Shirt only. A wider box reaches into trousers, chair and desk -- all of which correctly
# stay unchanged, which pushes the torso PSNR UP and makes a real garment change look like
# no change at all. Keep it on the fabric.
TORSO = (0.42, 0.57, 0.93, 0.90)
ROOM = (0.00, 0.00, 0.42, 0.85)

FACE_MIN_PSNR = 34.0     # below this the face has moved -- reject
ROOM_MIN_PSNR = 32.0     # below this the edit reached the room -- reject
TORSO_MAX_PSNR = 28.0    # ABOVE this the garment did not really change


def box(img: np.ndarray, frac) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = frac
    return img[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse < 1e-9 else 10 * np.log10((255.0 ** 2) / mse)


def parse_frac(s: str):
    v = tuple(float(x) for x in s.split(","))
    if len(v) != 4:
        raise argparse.ArgumentTypeError("need x0,y0,x1,y1 as fractions")
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--locked", required=True, type=Path)
    ap.add_argument("--variant", required=True, type=Path)
    ap.add_argument("--face", type=parse_frac, default=FACE)
    ap.add_argument("--torso", type=parse_frac, default=TORSO)
    ap.add_argument("--room", type=parse_frac, default=ROOM)
    ap.add_argument("--overlay", type=Path, help="where to write the debug overlay")
    args = ap.parse_args()

    for p in (args.locked, args.variant):
        if not p.exists():
            sys.exit("ERROR: missing %s" % p)

    A = Image.open(args.locked).convert("RGB")
    B = Image.open(args.variant).convert("RGB")
    if A.size != B.size:
        print("NOTE: variant is %s, locked is %s -- resizing variant to compare."
              % (B.size, A.size))
        B = B.resize(A.size)
    a, b = np.array(A), np.array(B)

    rows = [
        ("face", args.face, psnr(box(a, args.face), box(b, args.face)),
         ">=%.0f" % FACE_MIN_PSNR, lambda v: v >= FACE_MIN_PSNR,
         "identity + skin detail held"),
        ("room", args.room, psnr(box(a, args.room), box(b, args.room)),
         ">=%.0f" % ROOM_MIN_PSNR, lambda v: v >= ROOM_MIN_PSNR,
         "desk, monitor and light unchanged"),
        ("torso", args.torso, psnr(box(a, args.torso), box(b, args.torso)),
         "<=%.0f" % TORSO_MAX_PSNR, lambda v: v <= TORSO_MAX_PSNR,
         "the garment actually changed"),
    ]

    print("%-7s %-8s %-8s %-6s %s" % ("region", "PSNR", "want", "ok", "meaning"))
    ok = True
    for name, _f, v, want, test, why in rows:
        good = test(v)
        ok = ok and good
        print("%-7s %-8.1f %-8s %-6s %s" % (name, v, want, "yes" if good else "NO", why))

    out = args.overlay or args.variant.with_name(args.variant.stem + "-boxes.jpg")
    dbg = B.copy()
    d = ImageDraw.Draw(dbg)
    for name, f, _v, _w, _t, _why in rows:
        h, w = a.shape[:2]
        d.rectangle([int(w * f[0]), int(h * f[1]), int(w * f[2]), int(h * f[3])],
                    outline=(255, 0, 0), width=6)
        d.text((int(w * f[0]) + 10, int(h * f[1]) + 10), name, fill=(255, 255, 0))
    dbg.save(out, quality=92)
    print("\noverlay -> %s" % out)
    print("LOOK AT IT. Confident numbers from a box on the wrong thing are still wrong.")

    print("\n%s" % ("ACCEPT" if ok else "REJECT -- re-edit; do not generate from this"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
