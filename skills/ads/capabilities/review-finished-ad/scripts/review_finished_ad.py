#!/usr/bin/env python3
"""review-finished-ad: the finished-ad + brand-fidelity gate for a rendered video ad.

Runs the checks a machine can decide on a finished master, and builds one contact
sheet for the checks that need eyes (font, product likeness, safe zones):

  ratio        output is exactly the expected size (9:16 = 1080x1920)
  hook         sound starts within --hook-audio-s, and the opening is not a still frame
  pacing       no frozen stretch longer than --max-freeze-s (end card excluded)
  dead_air     no silence longer than --max-silence-s mid-video
  black_frames no black stretch longer than 0.3s
  logo_asset   the kit logo file is big enough to be a real logo (not a favicon)
  logo         the kit logo is found on the end card (multi-scale match, colour-blind)
  palette      the end card's main colours sit near the kit palette (warn only)

Exit codes: 0 PASS, 2 FAIL (a machine check failed), 3 ERROR (could not run).
Needs ffmpeg/ffprobe on PATH and Python packages numpy + pillow.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover - environment guard
    print(f"ERROR: {exc}. Install with: pip install --quiet numpy pillow", file=sys.stderr)
    sys.exit(3)

PASS, FAIL, WARN, NA = "pass", "fail", "warn", "not_applicable"

# Platform UI covers these bands on TikTok / Reels / Shorts at 1080x1920 (px).
SAFE_TOP, SAFE_BOTTOM, SAFE_RIGHT = 220, 400, 140

# Per match mode: (pass at or above, fail below). A whole-image match of the right logo
# scores ~0.95 even after video compression; a different logo on a similar background ~0.5-0.65.
LOGO_THRESHOLDS = {"silhouette": (0.70, 0.55), "image": (0.85, 0.70)}
MIN_LOGO_SHORT_SIDE = 256
PALETTE_WARN_DELTA_E = 25.0


@dataclass
class Check:
    status: str
    note: str = ""
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------- ffmpeg helpers

def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(video: str) -> dict:
    out = run([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height:format=duration", "-of", "json", video,
    ])
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()[:300]}")
    info = json.loads(out.stdout)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError("no video stream")
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "duration": float(info.get("format", {}).get("duration") or 0),
        "has_audio": has_audio,
    }


def analyse(video: str, has_audio: bool, silence_d: float, scene_threshold: float = 0.3) -> str:
    """ONE decode pass: freeze, black and scene-cut detection on a small copy of the
    picture, silence detection on the audio. Returns ffmpeg's log."""
    vf = (f"scale=270:-2,freezedetect=n=-60dB:d=1.0,blackdetect=d=0.2:pix_th=0.10,"
          f"select='gt(scene,{scene_threshold})',showinfo")
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-filter:v", vf]
    if has_audio:
        cmd += ["-filter:a", f"silencedetect=n=-45dB:d={silence_d:.2f}"]
    cmd += ["-f", "null", "-"]
    return run(cmd).stderr


def spans(log: str, key: str, duration: float) -> list[tuple[float, float]]:
    """Parse ffmpeg *detect start/end pairs (silence_, freeze_, black_)."""
    out: list[tuple[float, float]] = []
    start = None
    for line in log.splitlines():
        m = re.search(rf"{key}_start:\s*([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(rf"{key}_end:\s*([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, duration))
    return out


def grab(video: str, t: float, dest: Path) -> Path:
    """One frame at t. The container can run longer than the picture (an audio tail past the
    last frame), so a grab near the end steps back until a frame exists."""
    for back in (0.0, 0.3, 0.8, 1.5, 3.0):
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(t - back, 0):.3f}",
             "-i", video, "-frames:v", "1", str(dest)])
        if dest.exists():
            return dest
    raise RuntimeError(f"could not grab a frame at {t:.2f}s")


# ---------------------------------------------------------------- image helpers

def gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float64)


def flatten(img: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    img = img.convert("RGBA")
    base = Image.new("RGBA", img.size, bg + (255,))
    base.alpha_composite(img)
    return base.convert("RGB")


def trim_to_content(img: Image.Image) -> Image.Image:
    """Crop transparent (or flat) padding around a logo so it matches tightly."""
    rgba = img.convert("RGBA")
    alpha = np.asarray(rgba)[:, :, 3]
    if alpha.min() < 250:
        ys, xs = np.nonzero(alpha > 16)
    else:
        g = gray(rgba)
        corner = np.median([g[0, 0], g[0, -1], g[-1, 0], g[-1, -1]])
        ys, xs = np.nonzero(np.abs(g - corner) > 12)
    if len(xs) == 0:
        return rgba
    return rgba.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))


def fast_len(n: int) -> int:
    """Smallest 2^a*3^b*5^c >= n: pocketfft is slow on sizes with large prime factors."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


class Matcher:
    """Normalised cross-correlation of many templates over ONE image. The image's FFT
    and running sums are computed once, so a multi-scale search stays fast."""

    def __init__(self, image: np.ndarray, max_th: int, max_tw: int):
        self.image = image
        ih, iw = image.shape
        self.shape = (fast_len(ih + max_th), fast_len(iw + max_tw))
        self.fft = np.fft.rfft2(image, self.shape)
        self.ii = np.pad(image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        self.ii2 = np.pad(image ** 2, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def ncc(self, tpl: np.ndarray) -> np.ndarray:
        ih, iw = self.image.shape
        th, tw = tpl.shape
        if th > ih or tw > iw or th < 4 or tw < 4:
            return np.zeros((1, 1))
        t0 = tpl - tpl.mean()
        tnorm = math.sqrt(float((t0 ** 2).sum()))
        if tnorm < 1e-6:
            return np.zeros((1, 1))
        corr = np.fft.irfft2(self.fft * np.fft.rfft2(t0[::-1, ::-1], self.shape), self.shape)
        corr = corr[th - 1:ih, tw - 1:iw]
        n = th * tw

        def window(s: np.ndarray) -> np.ndarray:
            return s[th:, tw:] - s[:-th, tw:] - s[th:, :-tw] + s[:-th, :-tw]

        s1, s2 = window(self.ii), window(self.ii2)
        denom = np.sqrt(np.maximum(s2 - s1 ** 2 / n, 0)) * tnorm
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(denom > 1e-6 * tnorm, corr / denom, 0.0)


def edges(a: np.ndarray) -> np.ndarray:
    gy, gx = np.gradient(a)
    return np.hypot(gx, gy)


def otsu(a: np.ndarray) -> float:
    hist, bins = np.histogram(a, 64)
    centres = (bins[:-1] + bins[1:]) / 2
    w = hist.cumsum()
    m = (hist * centres).cumsum()
    best, thr = -1.0, float(centres[0])
    for i in range(len(hist) - 1):
        w0, w1 = w[i], w[-1] - w[i]
        if w0 == 0 or w1 == 0:
            continue
        v = w0 * w1 * (m[i] / w0 - (m[-1] - m[i]) / w1) ** 2
        if v > best:
            best, thr = v, float(centres[i])
    return thr


def has_transparency(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "P") and np.asarray(img.convert("RGBA"))[:, :, 3].min() < 250


def _search(f: np.ndarray, tpl_gray: np.ndarray, aspect: float, work_width: int, use_edges: bool):
    """Best (score, box) of the template over f across 16 sizes, in work-width pixels."""
    widths = [int(w) for w in np.unique(np.geomspace(20, work_width * 0.9, 16).astype(int))]
    image = edges(f) if use_edges else f
    matcher = Matcher(image, max(4, round(max(widths) * aspect)) + 1, max(widths))
    best_score, best_box = 0.0, None
    for width in widths:
        height = max(4, round(width * aspect))
        if height >= f.shape[0]:
            continue
        tpl = np.asarray(Image.fromarray(tpl_gray.astype(np.uint8)).resize((width, height), Image.BILINEAR),
                         dtype=np.float64)
        m = matcher.ncc(edges(tpl) if use_edges else tpl)
        y, x = np.unravel_index(int(np.argmax(m)), m.shape)
        if m[y, x] > best_score:
            best_score, best_box = float(m[y, x]), (int(x), int(y), width, height)
    return best_score, best_box


def find_logo(frame: Image.Image, logo: Image.Image, work_width: int = 360) -> dict:
    """Find the kit logo in a frame. `score` is 0-1.

    - An OPAQUE logo file (a JPEG, a photo mascot, a square app icon) is composited as the
      whole image, so it is matched as the whole image: multi-scale grayscale correlation.
    - A TRANSPARENT logo (a PNG/SVG mark) sits on any background in any colour, so:
      1. edge correlation picks the place and size (edges ignore colour: a white or
         recoloured version of the logo is still found), then
      2. the logo's own silhouette is compared with the frame crop binarised at that spot
         (either polarity). The overlap (IoU) is the score, so a different logo in the same
         place scores low even when its edges correlate.
    """
    scale = work_width / frame.width
    f = gray(frame.resize((work_width, max(1, round(frame.height * scale))), Image.BILINEAR))
    if not has_transparency(logo):
        rgb = logo.convert("RGB")
        score, box = _search(f, gray(rgb), rgb.height / rgb.width, work_width, use_edges=False)
        if box is None:
            return {"score": 0.0, "mode": "image", "bbox": None}
        x, y, w, h = box
        return {"score": round(max(score, 0.0), 3), "mode": "image",
                "bbox": [round(x / scale), round(y / scale), round(w / scale), round(h / scale)]}

    logo = trim_to_content(logo)
    alpha = np.asarray(logo)[:, :, 3] > 16
    lg = gray(flatten(logo, (255, 255, 255)))
    corr, box = _search(f, lg, logo.height / logo.width, work_width, use_edges=True)
    if box is None:
        return {"score": 0.0, "mode": "silhouette", "edge_corr": 0.0, "bbox": None}
    x, y, w, h = box
    crop = f[y:y + h, x:x + w]
    mask = np.asarray(Image.fromarray(alpha.astype(np.uint8) * 255).resize((w, h))) > 127
    dark = crop < otsu(crop)
    iou = max(float((mask & b).sum()) / max(float((mask | b).sum()), 1.0) for b in (dark, ~dark))
    return {
        "score": round(iou, 3),
        "mode": "silhouette",
        "edge_corr": round(corr, 3),
        "bbox": [round(x / scale), round(y / scale), round(w / scale), round(h / scale)],
    }


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = rgb_to_lab(a), rgb_to_lab(b)
    return math.dist(la, lb)


def dominant_colours(img: Image.Image, k: int = 5) -> list[tuple[tuple[int, int, int], float]]:
    small = img.convert("RGB").resize((160, 284))
    q = small.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: k * 3]
    counts = sorted(q.getcolors(), reverse=True)
    total = sum(c for c, _ in counts)
    return [((pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]), c / total) for c, i in counts]


# ---------------------------------------------------------------- checks

def check_ratio(meta: dict, expect: tuple[int, int]) -> Check:
    w, h = meta["width"], meta["height"]
    if (w, h) == expect:
        return Check(PASS, f"{w}x{h}")
    return Check(FAIL, f"{w}x{h}, expected {expect[0]}x{expect[1]}: scale and pad every clip before the concat")


def check_hook(meta: dict, silences, freezes, hook_audio_s: float) -> Check:
    problems = []
    lead = next((e for s, e in silences if s <= 0.05), 0.0)
    if meta["has_audio"] and lead > hook_audio_s:
        problems.append(f"no sound for the first {lead:.1f}s")
    if not meta["has_audio"]:
        problems.append("no audio track")
    still = next((e - s for s, e in freezes if s <= 0.3), 0.0)
    if still > 1.5:
        problems.append(f"opening frame is still for {still:.1f}s")
    if problems:
        return Check(FAIL, "; ".join(problems) + ": the first 2s must move and speak", {"lead_silence_s": lead})
    return Check(PASS, f"sound at {lead:.1f}s, opening moves", {"lead_silence_s": lead})


def check_pacing(meta: dict, freezes, cuts, max_freeze_s: float, endcard_s: float) -> Check:
    body_end = meta["duration"] - endcard_s
    long_freezes = [(s, e) for s, e in freezes if s < body_end and (min(e, body_end) - s) > max_freeze_s]
    shots = [b - a for a, b in zip([0.0] + cuts, cuts + [meta["duration"]])]
    data = {"cuts": len(cuts), "longest_shot_s": round(max(shots), 2) if shots else None,
            "frozen_spans": [[round(s, 2), round(e, 2)] for s, e in long_freezes]}
    if long_freezes:
        s, e = long_freezes[0]
        return Check(FAIL, f"picture frozen {s:.1f}-{e:.1f}s ({e - s:.1f}s): add motion or cut it", data)
    return Check(PASS, f"{len(cuts)} cuts, longest shot {data['longest_shot_s']}s", data)


def check_dead_air(meta: dict, silences, max_silence_s: float, endcard_s: float) -> Check:
    if not meta["has_audio"]:
        return Check(FAIL, "no audio track")
    # A silent end card is normal (most recipes append one), so silence that starts inside
    # the end-card window is not dead air. Anything that starts earlier is.
    body_end = meta["duration"] - endcard_s
    gaps = [(s, e) for s, e in silences if s > 0.3 and s < body_end and (min(e, body_end) - s) > max_silence_s]
    if gaps:
        s, e = gaps[0]
        return Check(FAIL, f"silence {s:.1f}-{e:.1f}s: tighten the VO or extend the music bed",
                     {"gaps": [[round(a, 2), round(b, 2)] for a, b in gaps]})
    return Check(PASS, "no dead air")


def check_black(blacks) -> Check:
    bad = [(s, e) for s, e in blacks if e - s > 0.3]
    if bad:
        s, e = bad[0]
        return Check(FAIL, f"black frames {s:.1f}-{e:.1f}s")
    return Check(PASS, "no black frames")


def check_logo_asset(logo: Image.Image | None) -> Check:
    if logo is None:
        return Check(NA, "no logo given")
    # An opaque logo (a JPEG mascot, an app icon) is used as the whole image; only a
    # transparent mark has padding to trim before judging its real size.
    trimmed = trim_to_content(logo) if has_transparency(logo) else logo
    short = min(trimmed.size)
    if short < MIN_LOGO_SHORT_SIDE and max(trimmed.size) < 2 * MIN_LOGO_SHORT_SIDE:
        return Check(FAIL, f"logo file is {trimmed.width}x{trimmed.height}: favicon-grade, it will be blurry. "
                           "Ask the user for a real logo, or set the wordmark as text in the brand font",
                     {"size": list(trimmed.size)})
    return Check(PASS, f"logo {trimmed.width}x{trimmed.height}", {"size": list(trimmed.size)})


def check_logo(frames: list[tuple[float, Image.Image]], logo: Image.Image | None) -> Check:
    if logo is None:
        return Check(NA, "no logo given")
    best = {"score": 0.0}
    for t, img in frames:
        m = find_logo(img, logo)
        if m["score"] > best["score"]:
            best = {**m, "t": round(t, 2)}
    s = best["score"]
    LOGO_PASS, LOGO_FAIL = LOGO_THRESHOLDS[best.get("mode", "silhouette")]
    if s >= LOGO_PASS:
        return Check(PASS, f"logo found at {best['t']}s (match {s:.2f})", best)
    if s < LOGO_FAIL:
        return Check(FAIL, f"kit logo not found on the end card (best match {s:.2f}): "
                           "composite the uploaded logo file, never a generated one", best)
    return Check(WARN, f"weak logo match {s:.2f}: check the sheet for a warped, cropped or redrawn logo", best)


def check_palette(frames: list[tuple[float, Image.Image]], palette: list[str]) -> Check:
    if not palette:
        return Check(NA, "no palette given")
    kit = [hex_to_rgb(h) for h in palette]
    img = frames[-1][1]
    dom = [c for c in dominant_colours(img) if c[1] >= 0.05][:4]
    dists = [min(delta_e(c, k) for k in kit) for c, _ in dom]
    nearest = min(dists) if dists else 999.0
    data = {"end_card_colours": ["#%02x%02x%02x" % c for c, _ in dom], "nearest_delta_e": round(nearest, 1)}
    if nearest <= PALETTE_WARN_DELTA_E:
        return Check(PASS, f"end card uses a kit colour (dE {nearest:.0f})", data)
    return Check(WARN, f"no kit colour on the end card (nearest dE {nearest:.0f}): check the sheet", data)


# ---------------------------------------------------------------- contact sheet

def safe_zone_overlay(img: Image.Image) -> Image.Image:
    img = img.convert("RGB").copy()
    w, h = img.size
    sx, sy = w / 1080, h / 1920
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    red = (255, 40, 40, 70)
    d.rectangle([0, 0, w, SAFE_TOP * sy], fill=red)
    d.rectangle([0, h - SAFE_BOTTOM * sy, w, h], fill=red)
    d.rectangle([w - SAFE_RIGHT * sx, 0, w, h], fill=red)
    return Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")


def font_specimen(font_path: str | None, text: str) -> Image.Image | None:
    if not font_path:
        return None
    try:
        font = ImageFont.truetype(font_path, 64)
    except OSError:
        return None
    img = Image.new("RGB", (540, 200), "white")
    ImageDraw.Draw(img).text((20, 60), text or "Aa Bb 123", font=font, fill="black")
    return img


def build_sheet(frames: list[tuple[float, Image.Image]], refs: list[tuple[str, Image.Image]], dest: Path) -> None:
    tw, th = 216, 384
    cols = min(6, max(1, len(frames)))
    rows = math.ceil(len(frames) / cols)
    ref_h = 200 if refs else 0
    sheet = Image.new("RGB", (cols * tw, rows * (th + 22) + ref_h + (22 if refs else 0)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (t, img) in enumerate(frames):
        x, y = (i % cols) * tw, (i // cols) * (th + 22)
        sheet.paste(safe_zone_overlay(img).resize((tw, th)), (x, y + 22))
        d.text((x + 4, y + 4), f"{t:.1f}s", fill="black")
    y0 = rows * (th + 22)
    if refs:
        d.text((4, y0 + 4), "REFERENCE (logo / product / font)", fill="black")
        x = 0
        for label, img in refs:
            im = img.convert("RGB")
            im.thumbnail((tw * 2 - 8, ref_h - 8))
            sheet.paste(im, (x + 4, y0 + 22 + 4))
            x += im.width + 12
            if x > sheet.width - 40:
                break
    sheet.save(dest)


# ---------------------------------------------------------------- main

def review(args: argparse.Namespace) -> dict:
    meta = probe(args.video)
    dur = meta["duration"]
    expect = tuple(int(v) for v in args.expect_size.lower().split("x"))
    log = analyse(args.video, meta["has_audio"], args.max_silence_s / 2)
    silences = spans(log, "silence", dur)
    freezes = spans(log, "freeze", dur)
    blacks = spans(log, "black", dur)
    cuts = [float(m) for m in re.findall(r"pts_time:([\d.]+)", log)]

    logo = Image.open(args.logo) if args.logo else None
    tmp = Path(tempfile.mkdtemp(prefix="rfa-"))
    # One frame per shot (mid-shot) + the end card.
    bounds = [0.0] + cuts + [dur]
    shot_times = [(a + b) / 2 for a, b in zip(bounds, bounds[1:]) if b - a > 0.2][:11]
    end_times = [max(0.0, dur - s) for s in (1.6, 0.9, 0.3)]
    frames = [(t, Image.open(grab(args.video, t, tmp / f"f{i:02d}.png")).convert("RGB"))
              for i, t in enumerate(shot_times)]
    end_frames = [(t, Image.open(grab(args.video, t, tmp / f"e{i}.png")).convert("RGB"))
                  for i, t in enumerate(end_times)]
    extra = [(t, Image.open(grab(args.video, t, tmp / f"x{i}.png")).convert("RGB"))
             for i, t in enumerate(args.logo_at or [])]

    checks = {
        "ratio": check_ratio(meta, expect),  # type: ignore[arg-type]
        "hook": check_hook(meta, silences, freezes, args.hook_audio_s),
        "pacing": check_pacing(meta, freezes, cuts, args.max_freeze_s, args.endcard_s),
        "dead_air": check_dead_air(meta, silences, args.max_silence_s, args.endcard_s),
        "black_frames": check_black(blacks),
        "logo_asset": check_logo_asset(logo),
        "logo": check_logo(end_frames + extra, logo),
        "palette": check_palette(end_frames, [p for p in (args.palette or "").split(",") if p.strip()]),
    }
    if args.speech is False:
        checks["dead_air"] = Check(NA, "--no-speech: music-only format")

    refs: list[tuple[str, Image.Image]] = []
    if logo is not None:
        refs.append(("logo", flatten(trim_to_content(logo), (255, 255, 255))))
    for p in (args.product_images or "").split(","):
        if p.strip() and Path(p.strip()).exists():
            refs.append(("product", Image.open(p.strip())))
    spec = font_specimen(args.font, args.brand_name or "")
    if spec is not None:
        refs.append(("font", spec))
    sheet = Path(args.sheet)
    sheet.parent.mkdir(parents=True, exist_ok=True)
    build_sheet(frames + end_frames[-1:], refs, sheet)

    failed = [k for k, c in checks.items() if c.status == FAIL]
    return {
        "verdict": "FAIL" if failed else "PASS",
        "failed": failed,
        "video": {**meta, "cuts": [round(c, 2) for c in cuts]},
        "checks": {k: asdict(c) for k, c in checks.items()},
        "sheet": str(sheet),
        "judge_on_sheet": [
            "safe_zones: no caption, CTA, price, logo or product name inside the red bands",
            "font: on-screen text uses the font in the reference specimen",
            "product_likeness: every product shot matches the reference product images (shape, label, colour)",
            "product_consistency: the product looks the same in every scene",
            "logo_unaltered: the logo is not warped, recoloured, cropped or redrawn",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--json", required=True, help="verdict JSON path")
    ap.add_argument("--sheet", default="working/review/finished-ad-sheet.png")
    ap.add_argument("--logo", help="the kit logo FILE the ad must show")
    ap.add_argument("--logo-at", type=float, action="append", help="extra timestamp(s) where the logo appears")
    ap.add_argument("--palette", help="comma-separated kit hex colours")
    ap.add_argument("--product-images", help="comma-separated product image files")
    ap.add_argument("--font", help="the brand font file (.ttf/.otf), for the specimen")
    ap.add_argument("--brand-name", help="text for the font specimen")
    ap.add_argument("--expect-size", default="1080x1920")
    ap.add_argument("--hook-audio-s", type=float, default=1.0)
    ap.add_argument("--max-freeze-s", type=float, default=2.5)
    ap.add_argument("--max-silence-s", type=float, default=1.0)
    ap.add_argument("--endcard-s", type=float, default=3.0)
    ap.add_argument("--no-speech", dest="speech", action="store_false",
                    help="format has no VO/dialogue (skip the dead-air check)")
    args = ap.parse_args(argv)

    if not Path(args.video).exists():
        print(f"ERROR: video not found: {args.video}", file=sys.stderr)
        return 3
    try:
        result = review(args)
    except Exception as exc:  # noqa: BLE001 - surface any environment failure as ERROR
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(result, indent=2))
    print(f"{result['verdict']}  failed={result['failed']}  sheet={result['sheet']}")
    for k, c in result["checks"].items():
        print(f"  {k:13s} {c['status']:15s} {c['note']}")
    return 2 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
