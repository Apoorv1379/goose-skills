#!/usr/bin/env python3
"""Measure character-clip takes side by side, for A/B-ing one video model against another.

Reports, per clip, the four things that decide whether a take ships:

  1. Container      resolution / fps / duration.
  2. Voice          median F0 + spread. The check behind critical knowledge #14 --
                    every clip in a series must land in the same narrow band.
  3. Audio          voice-band level, HF + sibilance relative to it (clarity), and the
                    noise floor (the ambience bed of critical knowledge #8). Band
                    definitions match clean-character-audio.sh so numbers are comparable.
  4. Stability      background-region PSNR against the clip's own first frame, the
                    method from critical knowledge #12. Static camera, so any drop is
                    real drift.

Plus a watermark assist: the bottom-right corner is cropped at each sample point and
the per-pixel temporal spread reported. A burned-in overlay barely moves while the
frame behind it does, so a LOW number there is the signature. Crops are saved for a
visual check -- this flags, it does not decide.

Costs nothing and calls no API. Run it on existing takes before generating anything.

Usage:
    ab_report.py CLIP [CLIP ...] [--frames-dir DIR] [--json OUT.json]
    ab_report.py veo-hook.mp4 h3-hook-seed1.mp4 h3-hook-seed2.mp4 --frames-dir ab-frames

Crop boxes default to fractions of each clip's own size, so clips of different
resolutions stay comparable. Override with --bg-crop / --wm-crop as W:H:X:Y fractions.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

NULL_SINK = "NUL" if os.name == "nt" else "/dev/null"

# Band definitions, identical to clean-character-audio.sh so the two agree.
VOX = "highpass=f=300,lowpass=f=3400"
HFB = "highpass=f=4000,lowpass=f=8000"
SIB = "highpass=f=8000,lowpass=f=14000"

# Locked character voice target, set with --target-f0. Empty means no voice screening.
TARGET_F0 = (None, 8.0)

# Defaults as fractions of frame size: W:H:X:Y
BG_CROP = (0.28, 0.22, 0.02, 0.05)   # top-left wall, away from the subject
WM_CROP = (0.36, 0.09, 0.62, 0.90)   # bottom-right, where watermarks land


def run(cmd: list[str]) -> str:
    """Run a command, returning stdout+stderr. ffmpeg writes its measurements to stderr."""
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return (p.stdout or "") + (p.stderr or "")


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_type,width,height,r_frame_rate",
               "-show_entries", "format=duration",
               "-of", "json", str(path)])
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return {}
    info = {"duration": float(d.get("format", {}).get("duration", 0) or 0),
            "has_audio": False, "width": 0, "height": 0, "fps": 0.0}
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and not info["width"]:
            info["width"] = int(s.get("width") or 0)
            info["height"] = int(s.get("height") or 0)
            num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
            den_f = float(den or 1)
            info["fps"] = round(float(num) / den_f, 3) if den_f else 0.0
        if s.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


# ---------------------------------------------------------------- audio

def band_db(path: Path, filt: str, start: float, dur: float) -> float | None:
    out = run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(dur), "-i", str(path),
               "-af", filt + ",volumedetect", "-f", "null", NULL_SINK])
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
    return float(m.group(1)) if m else None


def noise_floor(path: Path, start: float, dur: float) -> float | None:
    """The ambience bed: the 10th-percentile block RMS, i.e. the level in the gaps.

    Speech blocks sit far above this, so the low tail is the bed itself. Critical
    knowledge #8 measured Veo's at a steady -31..-35 dB plateau.
    """
    out = run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(dur), "-i", str(path),
               "-af", "astats=metadata=1:reset=8,"
                      "ametadata=print:key=lavfi.astats.Overall.RMS_level",
               "-f", "null", NULL_SINK])
    vals = [float(v) for v in re.findall(r"RMS_level=(-?[\d.]+)", out)]
    vals = sorted(v for v in vals if math.isfinite(v) and v > -90)
    if len(vals) < 3:
        return None
    return round(vals[max(0, int(len(vals) * 0.10))], 1)


def f0_stats(path: Path, start: float, dur: float) -> dict:
    """Median fundamental over voiced frames, by autocorrelation.

    Octave-halves on some takes, so a single number is indicative rather than exact --
    but it is reliable for the thing that matters here, which is whether two clips sit
    in the SAME band. Compare across clips; do not over-read one value.
    """
    tmp = Path(tempfile.mkdtemp(prefix="abf0_"))
    wav = tmp / "a.wav"
    try:
        run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", str(path),
             "-ac", "1", "-ar", "16000", "-f", "wav", str(wav), "-y"])
        if not wav.exists() or wav.stat().st_size < 1000:
            return {}
        import numpy as np
        with wave.open(str(wav)) as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        x = x.astype(np.float64) / 32768.0
        win, hop = int(0.04 * sr), int(0.01 * sr)
        lo, hi = int(sr / 300), int(sr / 70)          # 70-300 Hz search range
        vals = []
        for i in range(0, max(0, len(x) - win), hop):
            s = x[i:i + win]
            if np.sqrt(np.mean(s ** 2)) < 0.02:        # unvoiced / silence
                continue
            s = s - s.mean()
            r = np.correlate(s, s, "full")[win - 1:]
            if r[0] <= 0:
                continue
            r = r / r[0]
            seg = r[lo:hi]
            if not len(seg):
                continue
            k = int(np.argmax(seg)) + lo
            if r[k] < 0.35:                            # weak periodicity, not a pitch
                continue
            vals.append(sr / k)
        if len(vals) < 10:
            return {}
        v = np.array(vals)
        return {"median": round(float(np.median(v)), 1),
                "p25": round(float(np.percentile(v, 25)), 1),
                "p75": round(float(np.percentile(v, 75)), 1),
                "frames": len(v)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- video

def crop_expr(frac_box: tuple, w: int, h: int) -> str:
    cw, ch, cx, cy = frac_box
    return "crop=%d:%d:%d:%d" % (max(2, int(w * cw)), max(2, int(h * ch)),
                                 int(w * cx), int(h * cy))


def grab(path: Path, t: float, out: Path, vf: str) -> bool:
    run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-vf", vf, str(out), "-y"])
    return out.exists() and out.stat().st_size > 0


def psnr(a: Path, b: Path) -> float | None:
    out = run(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
               "-lavfi", "psnr", "-f", "null", NULL_SINK])
    m = re.search(r"average:([\d.]+|inf)", out)
    if not m:
        return None
    return float("inf") if m.group(1) == "inf" else round(float(m.group(1)), 2)


def info_duration(path: Path) -> float | None:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)]).strip().split()
    try:
        return float(out[0])
    except (ValueError, IndexError):
        return None


def camera_motion(path: Path, analysis_w: int = 960) -> dict:
    """Global frame-to-frame camera motion in px, scaled to a 1080-wide frame.

    Measured by phase correlation with sub-pixel refinement, which locks onto the
    dominant coherent motion across the frame -- for a mostly-static room that is the
    camera, not the subject.

    This exists because handheld shake and content drift look identical to a naive
    background-PSNR check: both move pixels. Reporting them separately is what lets a
    take be judged as "convincingly handheld" rather than "unstable". Reference points
    measured on this format: locked-off generations and the shipped manual episodes all
    sit at 0.01-0.04 px median. Anything below ~0.5 px is a tripod.
    """
    import numpy as np
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height", "-of", "default=nw=1:nk=1", str(path)]).split()
    try:
        w, h = int(out[0]), int(out[1])
    except (ValueError, IndexError):
        return {}
    H = int(round(h * analysis_w / w))
    H -= H % 2
    # Each pair costs two 2D FFTs, so cap the pair count rather than the clip length:
    # a 6s take samples at the full 12 fps, a finished 27s episode thins itself out.
    dur = float(info_duration(path) or 0)
    fps = 12.0 if dur <= 0 else min(12.0, max(2.0, 200.0 / dur))
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                        "fps=%.3f,scale=%d:%d,format=gray" % (fps, analysis_w, H),
                        "-f", "rawvideo", "-"], capture_output=True)
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(buf) // (analysis_w * H)
    if n < 3:
        return {}
    F = buf[:n * analysis_w * H].reshape(n, H, analysis_w).astype(np.float64)
    wy = np.hanning(H)[:, None]
    wx = np.hanning(analysis_w)[None, :]

    def refine(line, i, size):           # parabolic sub-pixel peak, wrapped
        a, b, c = line[(i - 1) % size], line[i], line[(i + 1) % size]
        den = a - 2 * b + c
        return 0.0 if den == 0 else 0.5 * (a - c) / den

    d, sharp = [], []
    for i in range(n - 1):
        fa = np.fft.rfft2(F[i] * wy * wx)
        fb = np.fft.rfft2(F[i + 1] * wy * wx)
        cs = fa * np.conj(fb)
        cs /= np.abs(cs) + 1e-9
        c = np.fft.irfft2(cs, s=F[i].shape)
        iy, ix = np.unravel_index(np.argmax(c), c.shape)
        dy = (iy - H if iy > H // 2 else iy) + refine(c[:, ix], iy, H)
        dx = (ix - analysis_w if ix > analysis_w // 2 else ix) + refine(
            c[iy, :], ix, analysis_w)
        d.append(math.hypot(dx, dy))
        # A real camera move gives one sharp correlation peak. Across a hard cut the two
        # frames share no structure, so the peak collapses into noise. Without this guard
        # a multi-shot file reports its edit as camera shake -- measured on a finished
        # episode, the cuts produced a 374 px "motion" spike.
        sharp.append(float(c.max() / (c.std() + 1e-12)))
    arr = np.array(d) * (1080.0 / analysis_w)
    sh = np.array(sharp)
    keep = sh >= max(8.0, float(np.median(sh)) * 0.25)
    cuts = int((~keep).sum())
    if keep.sum() >= 3:
        arr = arr[keep]
    return {"median": round(float(np.median(arr)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "max": round(float(arr.max()), 2),
            "cuts_excluded": cuts}


def motion_read(p90: float | None) -> str:
    """Bands calibrated 2026-09-05 against the generated shake ladder, not guessed.

    Reference points, median / p90 / max px on a 1080-wide frame:
        locked-off generations and both shipped manual episodes   0.02 / 0.04 / 0.08
        --shake subtle   (LOCKED for this series)                 1.86 / 3.44 / 5.07
        --shake natural                                           2.50 / 5.40 / 8.38
        --shake loose                                             2.84 / 5.34 / 20.59

    Note natural and loose are near-identical on median and p90 -- loose does not buy
    more handheld, only bigger occasional lurches (see its max). When judging a take,
    read `max` against `p90`: a max far above p90 is a bump, not a hand.
    """
    if p90 is None:
        return "-"
    if p90 < 0.5:
        return "tripod / locked off"
    if p90 < 3:
        return "gentle handheld"
    if p90 < 6:
        return "natural handheld"
    if p90 < 12:
        return "pronounced"
    return "too much"


def corner_spread(crops: list) -> float | None:
    """Mean per-pixel stddev across the sampled corner crops.

    Frame content moves; a burned-in overlay does not. A markedly LOW value next to
    another clip's is the watermark signature. Absolute values mean little alone.
    """
    import numpy as np
    stack = []
    for p in crops:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-f", "rawvideo",
                            "-pix_fmt", "gray", "-"], capture_output=True)
        if r.returncode == 0 and r.stdout:
            stack.append(np.frombuffer(r.stdout, dtype=np.uint8).astype(np.float32))
    if len(stack) < 2:
        return None
    n = min(len(s) for s in stack)
    arr = np.vstack([s[:n] for s in stack])
    return round(float(arr.std(axis=0).mean()), 2)


# ---------------------------------------------------------------- per clip

def analyse(path: Path, args) -> dict:
    res: dict = {"clip": path.name, "path": str(path)}
    info = probe(path)
    if not info:
        res["error"] = "ffprobe failed"
        return res
    for k in ("width", "height", "fps", "duration"):
        res[k] = info[k]

    start, dur = args.window
    if info["has_audio"]:
        vox = band_db(path, VOX, start, dur)
        hf = band_db(path, HFB, start, dur)
        sib = band_db(path, SIB, start, dur)
        res["voice_db"] = vox
        res["hf_rel"] = round(hf - vox, 1) if (hf is not None and vox is not None) else None
        res["sib_rel"] = round(sib - vox, 1) if (sib is not None and vox is not None) else None
        res["noise_floor"] = noise_floor(path, start, dur)
        res["f0"] = f0_stats(path, start, dur)
    else:
        res["error_audio"] = "no audio stream"

    w, h = info["width"], info["height"]
    if not (w and h):
        return res

    res["motion"] = camera_motion(path)

    keep = bool(args.frames_dir)
    frames_dir = (Path(args.frames_dir) / path.stem) if keep else Path(
        tempfile.mkdtemp(prefix="abfr_"))
    frames_dir.mkdir(parents=True, exist_ok=True)
    try:
        bg_vf = crop_expr(args.bg_crop, w, h)
        wm_vf = crop_expr(args.wm_crop, w, h)

        ref = frames_dir / "bg_t0.png"
        if grab(path, 0.04, ref, bg_vf):
            stab = {}
            for t in args.psnr_at:
                if t >= info["duration"]:
                    continue
                cur = frames_dir / ("bg_%s.png" % t)
                if grab(path, t, cur, bg_vf):
                    stab[str(t)] = psnr(cur, ref)
            vals = [v for v in stab.values() if v is not None and math.isfinite(v)]
            res["stability"] = stab
            res["stability_mean"] = round(sum(vals) / len(vals), 2) if vals else None

        wm = []
        for t in args.psnr_at:
            if t >= info["duration"]:
                continue
            p = frames_dir / ("wm_%s.png" % t)
            if grab(path, t, p, wm_vf):
                wm.append(p)
        res["corner_spread"] = corner_spread(wm)
        if keep:
            res["frames_dir"] = str(frames_dir)
    finally:
        if not keep:
            shutil.rmtree(frames_dir, ignore_errors=True)
    return res


# ---------------------------------------------------------------- output

def fmt(v, spec="{}", dash="-"):
    return dash if v is None else spec.format(v)


def report(rows: list) -> None:
    print("\n## Container\n")
    print("| clip | resolution | fps | duration |")
    print("|---|---|---|---|")
    for r in rows:
        print("| %s | %sx%s | %s | %s |" % (
            r["clip"], r.get("width", "?"), r.get("height", "?"),
            fmt(r.get("fps")), fmt(r.get("duration"), "{:.2f}s")))

    print("\n## Voice -- must land in the same band across every clip (knowledge #14)\n")
    print("| clip | median F0 | p25 | p75 | voiced frames |")
    print("|---|---|---|---|---|")
    for r in rows:
        f = r.get("f0") or {}
        print("| %s | %s | %s | %s | %s |" % (
            r["clip"], fmt(f.get("median"), "{} Hz"), fmt(f.get("p25")),
            fmt(f.get("p75")), fmt(f.get("frames"))))
    meds = [(r["clip"], (r.get("f0") or {}).get("median")) for r in rows]
    meds = [(c, m) for c, m in meds if m]
    if len(meds) > 1:
        spread = max(m for _, m in meds) - min(m for _, m in meds)
        verdict = "consistent" if spread <= 10 else (
            "borderline" if spread <= 25 else "DRIFTING")
        print("\n**F0 spread across clips: %.1f Hz -- %s.** Same-speaker takes land "
              "within a few Hz; tens of Hz apart is a different voice." % (spread, verdict))

    if TARGET_F0[0]:
        # Native-audio engines (Veo) cannot be given a voice reference, so matching the
        # locked character voice is a SELECTION problem: generate several, keep the take
        # nearest the target. This turns that into a pass/fail instead of a judgement.
        t, tol = TARGET_F0
        print("\n**Voice match against the locked character voice (%.1f Hz, +/-%.0f Hz):**\n"
              % (t, tol))
        print("| clip | median F0 | delta | |")
        print("|---|---|---|---|")
        for c, m in meds:
            d = m - t
            print("| %s | %.1f Hz | %+.1f Hz | %s |"
                  % (c, m, d, "PASS" if abs(d) <= tol else "reject"))

    print("\n## Audio quality -- bands match clean-character-audio.sh\n")
    print("| clip | voice band | HF rel | sibilance rel | noise floor |")
    print("|---|---|---|---|---|")
    for r in rows:
        print("| %s | %s | %s | %s | %s |" % (
            r["clip"], fmt(r.get("voice_db"), "{} dB"), fmt(r.get("hf_rel"), "{:+} dB"),
            fmt(r.get("sib_rel"), "{:+} dB"), fmt(r.get("noise_floor"), "{} dB")))
    print("\nHigher (less negative) HF/sibilance rel = clearer. A noise floor well below "
          "Veo's -31..-35 dB plateau means the cleanup step may not be needed at all.")

    print("\n## Camera motion -- is it convincingly handheld?\n")
    print("| clip | median px | p90 | max | cuts skipped | read |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        m = r.get("motion") or {}
        print("| %s | %s | %s | %s | %s | %s |" % (
            r["clip"], fmt(m.get("median")), fmt(m.get("p90")), fmt(m.get("max")),
            fmt(m.get("cuts_excluded")), motion_read(m.get("p90"))))
    print("\nGlobal frame-to-frame motion, scaled to a 1080-wide frame. Every clip made "
          "before shake was requested -- both shipped manual episodes included -- measured "
          "0.01-0.04 px median, i.e. tripod-locked. Read this table TOGETHER with the "
          "stability table below: camera motion and content drift both move pixels, and "
          "only this one separates them.")
    print("Judge by **median and p90**. On a multi-shot file the cut filter removes most "
          "but not all cut-adjacent pairs, so `max` can still carry a leftover spike from "
          "an edit rather than from the camera -- `cuts skipped` above zero means you are "
          "looking at an edited file, not a single take.")

    print("\n## Stability -- background PSNR vs the clip's own first frame (knowledge #12)\n")
    ts = sorted({t for r in rows for t in (r.get("stability") or {})}, key=float)
    print("| clip | " + " | ".join("%ss" % t for t in ts) + " | mean |")
    print("|---" * (len(ts) + 2) + "|")
    for r in rows:
        st = r.get("stability") or {}
        cells = " | ".join(fmt(st.get(t)) for t in ts)
        print("| %s | %s | %s |" % (r["clip"], cells, fmt(r.get("stability_mean"))))
    print("\n**On a handheld take this table under-reads.** A moving camera lowers PSNR "
          "even when nothing has drifted, so a low score here alongside healthy numbers in "
          "the camera-motion table means the shot moved, not that the character changed. "
          "Judge content drift on a handheld take by eye, or compare only takes shot at "
          "the same shake level.")
    print("\nHigher is steadier. **These numbers are only comparable within one run**, "
          "against clips measured with the same --bg-crop: PSNR depends heavily on how "
          "much detail the chosen region contains, so a flat wall scores far higher than "
          "a busy one. Do not compare them to the ~23 dB / ~20 dB figures in critical "
          "knowledge #12, which were measured on a different region. Compare clips to "
          "each other here, and re-measure the #12 baseline with this crop if you need "
          "an absolute reference.")

    print("\n## Watermark assist -- bottom-right corner\n")
    print("| clip | temporal spread |")
    print("|---|---|")
    for r in rows:
        print("| %s | %s |" % (r["clip"], fmt(r.get("corner_spread"))))
    print("\nA burned-in overlay holds still while the frame moves, so a markedly LOWER "
          "spread than the other clips is the signature. **Confirm visually** in the "
          "saved wm_*.png crops before concluding anything -- this flags, it does not decide.")
    print("Similar spreads across every clip usually mean no watermark is present *in "
          "this crop*. On 720x1280 Veo takes the default box lands on the subject, not "
          "on a mark -- pass --frames-dir and look, and widen --wm-crop to the full "
          "bottom strip (e.g. 1.0:0.14:0.0:0.86) if you are hunting for one.")


def frac(s: str):
    parts = [float(x) for x in s.split(":")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "expected W:H:X:Y as fractions, e.g. 0.28:0.22:0.02:0.05")
    return tuple(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--bg-crop", type=frac, default=BG_CROP,
                    help="background region as W:H:X:Y fractions (default top-left wall)")
    ap.add_argument("--wm-crop", type=frac, default=WM_CROP,
                    help="watermark region as W:H:X:Y fractions (default bottom-right)")
    ap.add_argument("--psnr-at", default="0.6,1.6,2.4",
                    help="sample points in seconds (default the knowledge #12 window)")
    ap.add_argument("--window", default="0.4:3.0",
                    help="audio analysis window START:DURATION (default 0.4:3.0)")
    ap.add_argument("--frames-dir", help="keep sampled frames here for visual inspection")
    ap.add_argument("--target-f0", type=float,
                    help="locked character voice in Hz. Takes are scored against it, "
                         "which is how voice consistency is held on an engine with no "
                         "audio reference (Veo). Episode 1 character: 153.8")
    ap.add_argument("--f0-tolerance", type=float, default=8.0,
                    help="Hz either side of --target-f0 that still passes (default 8)")
    ap.add_argument("--json", help="also write raw measurements to this path")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        sys.exit("ERROR: ffmpeg and ffprobe must be on PATH.")
    try:
        import numpy  # noqa: F401
    except ImportError:
        sys.exit("ERROR: numpy not installed. `pip install numpy`.")

    global TARGET_F0
    TARGET_F0 = (args.target_f0, args.f0_tolerance)
    args.psnr_at = [float(t) for t in args.psnr_at.split(",") if t.strip()]
    s, _, d = args.window.partition(":")
    args.window = (float(s), float(d or 3.0))

    missing = [c for c in args.clips if not c.exists()]
    if missing:
        sys.exit("ERROR: no such file: " + ", ".join(str(m) for m in missing))

    rows = []
    for c in args.clips:
        print("measuring %s ..." % c.name, file=sys.stderr)
        rows.append(analyse(c, args))
    report(rows)

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print("\nraw measurements -> %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
