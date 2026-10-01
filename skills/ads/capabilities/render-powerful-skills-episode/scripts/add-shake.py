#!/usr/bin/env python3
"""Add handheld camera shake to a locked-off take, calibrated to a target p90.

WHY THIS IS NEEDED
The prompt asks for handheld motion and Veo delivers it only on some seeds -- a lottery
like the music bed. Measured across three batches: episode 2's closer batch had 8 of 12
takes above 0.8 px p90, while episode 3's hook batch had 1 of 12 and a median of 0.04,
effectively tripod-still. And the one hook take that did have shake failed on voice, so
there was no seed that satisfied both. Episode 1's closer hit the same wall and had shake
added in post; this is that step, made repeatable.

Screen for shake FIRST (`screen-takes.py --min-shake`). Use this only when no take clears
both shake and voice -- a real handheld take always looks better than a synthesised one.

HOW
The frame is scaled up slightly to create margin, then cropped back with the crop window
driven by a sum of sines at incommensurate frequencies, so the motion never visibly
repeats. Amplitude is calibrated by measuring the result and rescaling once, because the
relationship between the sine amplitude and the measured motion depends on the clip.

Calibration is on the MEDIAN, not the p90 -- see apply() and main(). Episode 1's hook
measures median 0.70 / p90 2.28 and episode 2's 0.87 / 1.60, against 0.02-0.07 for a
locked-off take, so the default target sits in that band. Calibrated, not guessed: an
earlier hand-tuned attempt on episode 1 overshot to 5.9 before landing.

Usage:
    add-shake.py clip.mp4 --target 1.6
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ZOOM = 1.045          # margin for the crop to move within
FREQS = (0.63, 1.17, 1.9)
UPSCALE = 4           # see apply(): 2x still quantises, 4x does not
TARGET_MEDIAN = 0.80  # px. Real handheld MOVES ON EVERY FRAME -- see main().


def sh(cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True)


def motion_of(clip: Path) -> dict:
    import importlib.util as u
    spec = u.spec_from_file_location("ab", Path(__file__).resolve().parent / "ab_report.py")
    ab = u.module_from_spec(spec)
    spec.loader.exec_module(ab)
    try:
        return ab.camera_motion(clip) or {}
    except Exception:
        return {}


def apply(src: Path, out: Path, amp: float, w: int, h: int) -> bool:
    """Move the crop window at UPSCALE, then come back down.

    `crop` rounds its offsets to whole pixels, so the motion quantises. At native size a
    720x1280 take gave p90 0.06 at amp 0.41 and 3.16 at 0.6 -- nothing usable between. 2x
    halved the step, which was enough to hit a p90 target and NOT enough to fix the real
    problem: measured across the ladder, 2x holds the MEDIAN at 0.02-0.03 until amp 1.2 and
    then jumps it to 1.36. A clip like that is static on most frames with an occasional
    one-pixel snap, which is not what handheld looks like however good its p90 is (#49).
    At 4x the step is a quarter-pixel and the median rises smoothly, so it can be calibrated.
    """
    f1, f2, f3 = FREQS
    W2, H2 = w * UPSCALE, h * UPSCALE
    a = amp * UPSCALE
    x = ("(in_w-out_w)/2 + %.3f*sin(2*PI*%.3f*t) + %.3f*sin(2*PI*%.3f*t+2.1)"
         % (a, f1, a * 0.6, f2))
    y = ("(in_h-out_h)/2 + %.3f*sin(2*PI*%.3f*t+1.1) + %.3f*sin(2*PI*%.3f*t)"
         % (a * 0.8, f2, a * 0.5, f3))
    vf = ("scale=%d:%d,crop=%d:%d:x='%s':y='%s',scale=%d:%d"
          % (int(W2 * ZOOM) // 2 * 2, int(H2 * ZOOM) // 2 * 2, W2, H2, x, y, w, h))
    r = sh(["ffmpeg", "-v", "error", "-i", src, "-vf", vf,
            "-c:v", "libx264", "-crf", "17", "-preset", "slow",
            "-c:a", "copy", out, "-y"])
    if not out.exists() or out.stat().st_size < 10000:
        print("  ffmpeg: %s" % (r.stderr or "")[-300:], file=sys.stderr)
        return False
    return True


def donor_track(clip: Path, analysis_w: int = 480):
    """Per-frame (dx, dy) of a REAL handheld take, in output pixels.

    Three sines at incommensurate frequencies reproduce the right amount of motion but not
    its signature -- real handheld is broadband and irregular, a synthesised bed is smooth
    and pendulum-like. Replaying a measured trajectory from a take the operator already
    accepted (episode 1 or 2's hook) matches the reference by construction instead of by a
    summary statistic (#49, proposal 4).
    """
    import numpy as np
    # Pull the first two integers rather than splitting on ',' -- a file carrying an extra
    # stream (a cover image, say) returns "1920\n\n1080" and the naive split yields
    # "1920\n\n1080" as one field. Measured on episode 2's hook.
    out = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height", "-of", "csv=p=0", clip]).stdout
    nums = [int(t) for t in re.findall(r"\d+", out)][:2]
    if len(nums) < 2:
        return None
    w, h = nums
    H = int(round(h * analysis_w / w)) // 2 * 2
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-vf",
                        "scale=%d:%d,format=gray" % (analysis_w, H), "-f", "rawvideo", "-"],
                       capture_output=True)
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(buf) // (analysis_w * H)
    if n < 6:
        return None
    F = buf[:n * analysis_w * H].reshape(n, H, analysis_w).astype(np.float64)
    wy, wx = np.hanning(H)[:, None], np.hanning(analysis_w)[None, :]

    def refine(line, i, size):
        """Parabolic sub-pixel peak, wrapped -- the same refinement ab_report uses.

        Without this the integer argmax quantises the very thing being measured: handheld
        motion of ~0.9 px at 1080 is ~0.4 px at the 480 px analysis width, which rounds to
        zero on every frame. First attempt at this function omitted it and produced a track
        with x std EXACTLY 0.000.
        """
        a, b, c_ = line[(i - 1) % size], line[i], line[(i + 1) % size]
        den = a - 2 * b + c_
        return 0.0 if den == 0 else 0.5 * (a - c_) / den

    xs, ys, cx, cy = [], [], 0.0, 0.0
    for i in range(n - 1):
        fa = np.fft.rfft2(F[i] * wy * wx)
        fb = np.fft.rfft2(F[i + 1] * wy * wx)
        cs = fa * np.conj(fb)
        cs /= np.abs(cs) + 1e-9
        c = np.fft.irfft2(cs, s=F[i].shape)
        iy, ix = np.unravel_index(np.argmax(c), c.shape)
        dy = (iy - H if iy > H // 2 else iy) + refine(c[:, ix], iy, H)
        dx = (ix - analysis_w if ix > analysis_w // 2 else ix) + refine(c[iy, :], ix,
                                                                       analysis_w)
        cx += float(dx); cy += float(dy)
        xs.append(cx); ys.append(cy)
    xs, ys = np.array(xs), np.array(ys)
    # keep only the shake: remove the slow drift the take also contains
    t = np.arange(len(xs))
    for a in (xs, ys):
        a -= np.polyval(np.polyfit(t, a, 2), t)
    return xs * (w / analysis_w), ys * (w / analysis_w)


def apply_donor(src: Path, out: Path, track, gain: float, w: int, h: int) -> bool:
    """Drive the crop window frame by frame from a measured trajectory, via sendcmd."""
    import numpy as np, tempfile, os
    xs, ys = track
    fps = 30.0
    W2, H2 = w * UPSCALE, h * UPSCALE
    pad_w, pad_h = int(W2 * ZOOM) // 2 * 2, int(H2 * ZOOM) // 2 * 2
    bx, by = (pad_w - W2) / 2, (pad_h - H2) / 2
    lines = []
    for i in range(len(xs)):
        x = bx + float(np.clip(xs[i] * gain * UPSCALE, -bx, bx))
        y = by + float(np.clip(ys[i] * gain * UPSCALE, -by, by))
        lines.append("%.3f crop x %d, crop y %d;" % (i / fps, int(round(x)), int(round(y))))
    fd, cmds = tempfile.mkstemp(suffix=".cmd", text=True)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines))
    # sendcmd reads a filename from the filter graph, where ':' separates options and '\'
    # is an escape -- a Windows path needs both neutralised or the graph fails to parse.
    safe = cmds.replace("\\", "/").replace(":", "\\:")
    vf = ("scale=%d:%d,sendcmd=f='%s',crop=%d:%d:%d:%d,scale=%d:%d"
          % (pad_w, pad_h, safe, W2, H2, int(bx), int(by), w, h))
    r = sh(["ffmpeg", "-v", "error", "-i", src, "-vf", vf, "-c:v", "libx264",
            "-crf", "17", "-preset", "slow", "-c:a", "copy", out, "-y"])
    os.unlink(cmds)
    if not out.exists() or out.stat().st_size < 10000:
        print("  ffmpeg: %s" % (r.stderr or "")[-300:], file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--target", type=float, default=TARGET_MEDIAN,
                    help="MEDIAN px to aim for. Calibrated on the shipped hooks, which are "
                         "genuine handheld: episode 1 measured median 0.70 / p90 2.28 and "
                         "episode 2 median 0.87 / p90 1.60.")
    ap.add_argument("--tol", type=float, default=0.30)
    ap.add_argument("--donor", type=Path,
                    help="a shipped clip with REAL handheld motion; its measured "
                         "trajectory is replayed instead of synthesised sines. "
                         "Falls back to sines if the track cannot be measured.")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg not on PATH.")

    track = None
    if args.donor:
        if not args.donor.exists():
            sys.exit("ERROR: donor %s not found." % args.donor)
        track = donor_track(args.donor)
        if track is None:
            print("could not measure %s -- falling back to synthesised motion"
                  % args.donor.name, file=sys.stderr)
        else:
            print("donor %s: %d frames of measured handheld motion"
                  % (args.donor.name, len(track[0])))
    rc = 0
    for c in args.clips:
        if not c.exists():
            print("missing %s" % c)
            rc = 1
            continue
        wh = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=width,height", "-of", "csv=p=0", c]).stdout.strip().split(",")
        w, h = int(wh[0]), int(wh[1])
        b = motion_of(c)
        before = b.get("median")
        if before is not None and before >= args.target - args.tol:
            print("%-22s median %.2f already handheld -- left alone" % (c.name, before))
            continue

        # Calibrate on the MEDIAN, not the p90. p90 measures the biggest jolts, which a
        # quantised crop produces even when every other frame is frozen; the median
        # measures whether the camera is moving AT ALL on a typical frame, which is what
        # separates real handheld (0.70-0.87) from a tripod (0.02-0.07). Episodes 3 and 4
        # both passed a p90 bar at medians of 0.02-0.27 and read as locked off (#49).
        tmp = c.with_name(c.stem + "-sh" + c.suffix)
        keep = c.with_name(c.stem + "-best" + c.suffix)
        got, amp, stats = None, None, {}
        ladder = (0.5, 0.8, 1.1, 1.3, 1.5, 1.9, 2.4)
        for a in ladder:
            made = (apply_donor(c, tmp, track, a, w, h) if track is not None
                    else apply(c, tmp, a, w, h))
            if not made:
                continue
            mm = motion_of(tmp)
            v = mm.get("median")
            if v is None:
                continue
            if got is None or abs(v - args.target) < abs(got - args.target):
                got, amp, stats = v, a, mm
                keep.write_bytes(tmp.read_bytes())
            if v > args.target * 2.0:
                break
        if keep.exists():
            tmp.write_bytes(keep.read_bytes())
        keep.unlink(missing_ok=True)
        if got is None:
            print("%-22s FAILED" % c.name)
            rc = 1
            tmp.unlink(missing_ok=True)
            continue
        c.with_suffix(c.suffix + ".flat").write_bytes(c.read_bytes())
        c.write_bytes(tmp.read_bytes())
        tmp.unlink(missing_ok=True)
        ok = abs(got - args.target) <= args.tol
        print("%-22s median %.2f -> %.2f  p90 %.2f  (target %.2f, amp %.2f)  %s"
              % (c.name, before if before is not None else -1, got,
                 stats.get("p90", -1), args.target, amp, "ok" if ok else "off target"))
        if not ok:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
