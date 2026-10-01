#!/usr/bin/env python3
"""Check an outfit-variant PAIR before it is used to generate an episode (State 0b).

The variant route re-renders the frame rather than editing it, so pixel identity with the
locked reference is not available and `check-outfit.py`'s PSNR gate does not apply. What
still MUST hold, and what this measures:

  1. FRAMING  -- head width and head-top position within a few percent of the locked
     reference. Each generation reframes slightly; left unchecked, composition creeps
     across the series and episode 9 is visibly tighter than episode 3.
  2. SHIRT     -- the two images of a pair must agree on the garment colour. They are
     separate generations, so the hook and closer of one episode can otherwise ship
     wearing different shirts. Generate the second chained off the first to keep this
     tight.
  3. ROOM      -- the desk, monitor and blinds still correlate with the locked frame, so
     the model has not rebuilt the set.

Identity is NOT measured here. This route re-renders the face, so whether he still reads
as the same man is an operator judgement on a side-by-side -- that call cannot be faked
with a number, and pretending otherwise would be worse than admitting it.

Usage:
    check-variant-pair.py --locked-hook locked/character-selfie.png \\
        --locked-closer locked/character-closer-ref.png \\
        --hook outfits/ep03-04/hook.png --closer outfits/ep03-04/closer.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HEAD_TOL = 8.0      # % head-width change vs locked before framing has drifted
POS_TOL = 60        # px head-top movement on a 1920-tall frame
# Garment agreement is measured RELATIVE to brightness, not as a raw per-channel delta.
# An absolute threshold is structurally unfair to bright garments: the rust tee failed at
# 21.9 while the navy passed at 3.7, yet the navy had the LARGER hue difference of the two.
# Calibrated against the control -- the locked grey henley, unquestionably one shirt in one
# session, measures 12.5% across the two poses purely from pose and lighting. So anything
# under ~18% is within what the poses themselves explain.
COLOUR_TOL = 18.0   # % of mean brightness
ROOM_MIN = 0.80     # correlation of the room strip against locked


def head(img: Image.Image):
    """Head width and top edge, from the dark-hair mass. Locked character, light wall."""
    a = np.asarray(img.convert("L").resize((1080, 1920))).astype(float)
    band = a[int(1920 * 0.27):int(1920 * 0.47), 400:1080] < 70
    cs = np.nonzero(band.sum(0) > 8)[0]
    rs = np.nonzero(band.sum(1) > 8)[0]
    if len(cs) < 2 or len(rs) < 2:
        return None, None
    return float(cs.max() - cs.min()), float(int(1920 * 0.27) + rs.min())


def shirt(img: Image.Image):
    a = np.asarray(img.convert("RGB").resize((1080, 1920))).astype(float)
    r = a[int(1920 * 0.62):int(1920 * 0.85), int(1080 * 0.40):int(1080 * 0.85)].reshape(-1, 3)
    sel = r[np.abs(r - r.mean(0)).sum(1) < 120]      # drop skin and background outliers
    return (sel if len(sel) else r).mean(0)


def room_corr(a: Image.Image, b: Image.Image) -> float:
    """Correlate the desk/monitor/blinds strip.

    Sampled to x<0.35, not 0.42: a bulkier garment (an open hoodie) widens his silhouette
    into the wider strip, so the check ends up measuring his shoulder and reports a room
    change that never happened. That produced a false failure on the eps5-6 pair.
    """
    # ...and to the TOP 60% as well. Narrowing x alone was only half the fix: he sits in
    # the lower part of the frame at every x, so a top with long sleeves or a lower hem
    # reaches into the strip from below. The eps05-06 sweatshirt measured 0.771 over the
    # full height and 0.994 over y<60%, and a per-band diff put all of the change in the
    # bottom 40% while the blinds, monitor and desk above it differed by 2.6 of 255 (#137).
    X, Y = int(1080 * 0.35), int(1920 * 0.60)
    ga = np.asarray(a.convert("L").resize((1080, 1920))).astype(float)[:Y, :X]
    gb = np.asarray(b.convert("L").resize((1080, 1920))).astype(float)[:Y, :X]
    return float(np.corrcoef(ga.ravel(), gb.ravel())[0, 1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for a in ("locked-hook", "locked-closer", "hook", "closer"):
        ap.add_argument("--" + a, required=True, type=Path)
    args = ap.parse_args()
    for a in ("locked_hook", "locked_closer", "hook", "closer"):
        if not getattr(args, a).exists():
            sys.exit("ERROR: missing %s" % getattr(args, a))

    ok = True
    print("%-9s %-10s %-10s %-9s %s" % ("shot", "head w", "vs locked", "head top", "ok"))
    for name, lk, vr in (("hook", args.locked_hook, args.hook),
                         ("closer", args.locked_closer, args.closer)):
        L, V = Image.open(lk), Image.open(vr)
        lw, lt = head(L)
        vw, vt = head(V)
        if lw is None or vw is None:
            print("%-9s could not locate the head -- check by eye" % name)
            ok = False
            continue
        d = 100 * (vw / lw - 1)
        good = abs(d) <= HEAD_TOL and abs(vt - lt) <= POS_TOL
        ok = ok and good
        print("%-9s %-10.0f %-+10.1f%% %-9.0f %s"
              % (name, vw, d, vt - lt, "yes" if good else "NO"))

    ch, cc = shirt(Image.open(args.hook)), shirt(Image.open(args.closer))
    delta = 100 * float(np.abs(ch - cc).max()) / max(ch.mean(), cc.mean(), 1.0)
    good = delta <= COLOUR_TOL
    ok = ok and good
    print("\ngarment  hook %s  closer %s  delta %.1f%%  (<=%.0f%%, control 12.5%%)  %s"
          % (np.round(ch, 1), np.round(cc, 1), delta, COLOUR_TOL, "yes" if good else "NO"))

    for name, lk, vr in (("hook", args.locked_hook, args.hook),
                         ("closer", args.locked_closer, args.closer)):
        c = room_corr(Image.open(lk), Image.open(vr))
        g = c >= ROOM_MIN
        ok = ok and g
        print("room     %-7s correlation %.3f  (>=%.2f)  %s"
              % (name, c, ROOM_MIN, "yes" if g else "NO"))

    print("\n%s" % ("PASS -- now judge identity by eye on a side-by-side"
                    if ok else "FAIL -- re-roll the failing image"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
