#!/usr/bin/env python3
"""Trim each clip at the point the picture starts drifting, never before the line ends.

WHY THIS EXISTS
Veo degrades in the last stretch of every generation -- framing pulls, eyes go, the face
softens -- and it always happens AFTER the dialogue finishes, because the model paces the
line to fill the clip (SKILL.md #24). So every take needs the same cut: keep all of the
speech, drop the drift. Done by eye that is 36+ judgement calls across ten episodes, and
it is the kind of repetitive check that gets sloppy near the end of a batch.

HOW THE CUT IS CHOSEN
Two hard rules and one measurement:

  1. NEVER cut before the line completes. The floor is speech-end plus --pad, always.
  2. Never cut later than the clip.
  3. Between those, cut at the first frame where the picture has visibly moved on from
     how it looked during the speech.

"Moved on" is measured two ways, because the failure shows up in both:

  * FRAMING -- phase-correlation displacement against a baseline frame taken mid-speech.
    Catches the pull-back and the wander.
  * CONTENT -- PSNR of the face region against that same baseline. Catches the softening,
    the eye distortion and the morphing, which move few pixels but change them a lot.

The baseline is taken at 60% through the spoken section: late enough to be representative,
early enough to be clean.

For a VO clip the picture is discarded, so pass --audio-only: it cuts on speech end alone
and never inspects frames.

Usage:
    trim-tail.py CLIP [CLIP ...] [--out-dir DIR] [--pad 0.25] [--audio-only] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

# Framing is judged on a SUSTAINED trend, not one frame: the intended handheld shake
CHANGE_MARGIN = 0.30       # s to back off from a detected face change, so the cut lands
                           # on the last GOOD frame rather than the first bad one (#56)

# moves the frame 10-24px and returns, while real drift moves and stays. Requiring it
# to hold for 0.5s stops a wander that comes back from triggering a cut.
DRIFT_PX = 26.0       # sustained displacement on a 1080-wide frame
DRIFT_WIN = 6         # consecutive frames at 12fps that must exceed it
PSNR_DROP = 3.0       # dB below the in-speech baseline, AFTER cancelling camera shift


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def dur(p: Path) -> float:
    try:
        return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=nw=1:nk=1", str(p)]).stdout.strip())
    except ValueError:
        return 0.0


def speech_window(clip: Path):
    import numpy as np
    tmp = Path(tempfile.mkdtemp(prefix="tt_"))
    try:
        w = tmp / "a.wav"
        sh(["ffmpeg", "-v", "error", "-i", str(clip), "-vn", "-ac", "1",
            "-ar", "16000", str(w), "-y"])
        if not w.exists():
            return None, None
        with wave.open(str(w)) as f:
            sr = f.getframerate()
            x = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
        x = x.astype(float) / 32768.0
        n = int(sr * 0.02)
        t, v = [], []
        for i in range(0, len(x) - n, n):
            seg = x[i:i + n]
            m = np.abs(np.fft.rfft(seg * np.hanning(n)))
            fr = np.fft.rfftfreq(n, 1 / sr)
            t.append(i / sr)
            v.append(m[(fr > 300) & (fr < 3400)].sum())
        t, v = np.array(t), np.array(v)
        if v.max() <= 0:
            return None, None
        sp = t[v > v.max() * 0.05]
        return float(sp.min()), float(sp.max())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def line_end(clip: Path, line: str):
    """When the SCRIPTED line finishes, from word-level ASR timings.

    This is the whole point of the tool. An energy-based speech detector measures where
    ALL vocalisation stops, and Veo routinely adds something after the line -- a laugh, a
    stray word, a hum. On one take the line ended at 4.24s and the detector reported 5.92s
    because it was measuring a [laughs] at 5.18-5.72s, so the trimmer concluded there was
    nothing to cut and left 1.7s of drift and babble in the clip.

    Matching the transcript's words against the script gives the real end of the line.
    Returns (end_seconds, extra_text_after) or (None, None) if it cannot be determined.
    """
    if "FAL_KEY" not in os.environ and "FAL_API_KEY" in os.environ:
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
    if not os.environ.get("FAL_KEY"):
        return None, None
    try:
        from fal_client import subscribe, upload_file
    except ImportError:
        return None, None
    tmp = Path(tempfile.mkdtemp(prefix="tle_"))
    try:
        m = tmp / "a.mp3"
        sh(["ffmpeg", "-v", "error", "-i", str(clip), "-vn", "-ac", "1",
            "-ar", "44100", str(m), "-y"])
        if not m.exists():
            return None, None
        r = subscribe("fal-ai/elevenlabs/speech-to-text/scribe-v2",
                      arguments={"audio_url": upload_file(str(m))}, with_logs=False)
    except Exception:
        return None, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    words = [w for w in ((r or {}).get("words") or [])
             if tokens(w.get("text", ""))]
    if not words:
        return None, None
    want = tokens(line)
    if not want:
        return None, None

    # Walk the transcript, advancing through the script; the last script token matched
    # gives the end of the line. A single transcript word can carry several tokens
    # ("100" -> one, hundred), so consume as many as it matches.
    wi, last_end, last_idx = 0, None, -1
    for i, w in enumerate(words):
        matched = False
        for tok in tokens(w.get("text", "")):
            if wi >= len(want):
                break
            # "a hundred" for "one hundred" -- the phrase rewrite in tokens() cannot catch
            # this because ASR delivers "a" and "hundred" as separate words.
            article = (tok == "a" and want[wi] == "one"
                       and wi + 1 < len(want) and want[wi + 1] in ("hundred", "thousand"))
            if tok == want[wi] or article:
                wi += 1
                matched = True
            else:
                break
        if matched:
            last_end = float(w.get("end", 0) or 0)
            last_idx = i
            if wi == len(want):
                break
    if last_end is None or wi < max(2, int(len(want) * 0.6)):
        return None, None
    extra = " ".join(w.get("text", "").strip() for w in words[last_idx + 1:]).strip()
    return last_end, extra


# Number spelling, kept identical to screen-takes.py's. A transcript says "eight" where the
# line reads "8", and a literal match rejects the take. Duplicated rather than imported
# because both filenames are hyphenated; if you change one, change the other.
_NUM = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
        7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
        13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen",
        18: "eighteen", 19: "nineteen", 20: "twenty", 30: "thirty", 40: "forty",
        50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}


def _spell(n: int) -> str:
    """Enough number-to-words to match how a model reads a line aloud."""
    if n in _NUM:
        return _NUM[n]
    if n < 100:
        return (_NUM[n // 10 * 10] + " " + _NUM[n % 10]).strip()
    if n < 1000:
        head = _NUM[n // 100] + " hundred"
        return head if n % 100 == 0 else head + " " + _spell(n % 100)
    if n < 1000000:
        head = _spell(n // 1000) + " thousand"
        return head if n % 1000 == 0 else head + " " + _spell(n % 1000)
    return str(n)


def tokens(s: str) -> list[str]:
    """Normalise to comparable word tokens, spelling any numerals out.

    Returns a LIST because one input word can become several tokens: "100" -> ["one",
    "hundred"]. Both the script line and each transcript word go through this, so the two
    sides tokenise the same way whichever of them wrote the number as a numeral.
    """
    import re
    t = (s or "").lower()
    t = re.sub(r"\d+", lambda m: " " + _spell(int(m.group())) + " ", t)
    t = re.sub(r"\ba hundred\b", "one hundred", t)
    t = re.sub(r"\ba thousand\b", "one thousand", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return t.split()


def norm_word(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def frames_gray(clip: Path, start: float, width: int = 320):
    """Grayscale frames from `start` to the end, at 12 fps."""
    import numpy as np
    out = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height", "-of", "default=nw=1:nk=1", str(clip)]).stdout.split()
    w, h = int(out[0]), int(out[1])
    H = int(round(h * width / w)); H -= H % 2
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % start, "-i", str(clip),
                        "-vf", "fps=12,scale=%d:%d,format=gray" % (width, H),
                        "-f", "rawvideo", "-"], capture_output=True)
    b = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(b) // (width * H)
    if n < 2:
        return None, None, None
    return b[:n * width * H].reshape(n, H, width).astype(float), width, H


def displacement(a, b, W, H):
    import numpy as np
    wy, wx = np.hanning(H)[:, None], np.hanning(W)[None, :]
    fa, fb = np.fft.rfft2(a * wy * wx), np.fft.rfft2(b * wy * wx)
    cs = fa * np.conj(fb)
    cs /= np.abs(cs) + 1e-9
    c = np.fft.irfft2(cs, s=a.shape)
    iy, ix = np.unravel_index(np.argmax(c), c.shape)
    dy = iy - H if iy > H // 2 else iy
    dx = ix - W if ix > W // 2 else ix
    return float((dx ** 2 + dy ** 2) ** 0.5) * (1080.0 / W)


def shift_int(a, dx: int, dy: int):
    """Move a frame back by an integer offset, so content can be compared in place."""
    import numpy as np
    return np.roll(np.roll(a, -dy, axis=0), -dx, axis=1)


def displacement_xy(a, b, W, H):
    import numpy as np
    wy, wx = np.hanning(H)[:, None], np.hanning(W)[None, :]
    fa, fb = np.fft.rfft2(a * wy * wx), np.fft.rfft2(b * wy * wx)
    cs = fa * np.conj(fb)
    cs /= np.abs(cs) + 1e-9
    c = np.fft.irfft2(cs, s=a.shape)
    iy, ix = np.unravel_index(np.argmax(c), c.shape)
    dy = iy - H if iy > H // 2 else iy
    dx = ix - W if ix > W // 2 else ix
    return int(dx), int(dy)


def face_psnr(a, b):
    """Content difference in the head region, AFTER cancelling the camera shift.

    This distinguishes degradation from handheld movement, which the raw comparison
    cannot: the intended shake moves the frame 10-24px and swamped an uncompensated
    measure entirely. Aligning first leaves only what actually changed.
    """
    import numpy as np
    H, W = a.shape
    dx, dy = displacement_xy(a, b, W, H)
    a2 = shift_int(a, dx, dy)
    ya, yb = int(H * 0.28), int(H * 0.62)
    xa, xb = int(W * 0.42), int(W * 0.86)
    # ignore a margin the roll wrapped around
    mx, my = min(abs(dx) + 2, (xb - xa) // 3), min(abs(dy) + 2, (yb - ya) // 3)
    d = a2[ya + my:yb - my, xa + mx:xb - mx] - b[ya + my:yb - my, xa + mx:xb - mx]
    if d.size == 0:
        return 99.0
    mse = float(np.mean(d ** 2))
    return 99.0 if mse <= 1e-9 else 10 * np.log10((255.0 ** 2) / mse)


def eye_openness(frame):
    """How much structure is in the eye band. Open eyes have iris/sclera contrast;
    a closed lid flattens it, so a low value means the frame lands on a blink."""
    import numpy as np
    H, W = frame.shape
    band = frame[int(H * 0.40):int(H * 0.47), int(W * 0.46):int(W * 0.76)]
    return float(band.std()) if band.size else 0.0


def back_off_to_open_eyes(F, fps, cut_t, floor_t):
    """Walk the cut back to the last frame with open eyes, never past the line end.

    The drift detector picks where the picture starts changing, which is often mid-blink
    -- the clip then ends on closed eyes, which reads as a mistake. This nudges the cut
    earlier to land on a frame where he is looking at the lens.
    """
    import numpy as np
    lo, hi = int(floor_t * fps), min(int(cut_t * fps), len(F) - 1)
    if hi <= lo:
        return cut_t, ""
    vals = [eye_openness(F[i]) for i in range(lo, hi + 1)]
    open_ref = float(np.percentile(vals, 75))
    for i in range(hi, lo - 1, -1):
        if vals[i - lo] >= open_ref * 0.85:
            t = i / fps
            return t, ("" if abs(t - cut_t) < 1e-6
                       else "; backed off %.2fs to open eyes" % (cut_t - t))
    return cut_t, "; no open-eye frame found in range"


def find_cut(clip: Path, pad: float, audio_only: bool, line: str = ""):
    import numpy as np
    total = dur(clip)
    s0, s1 = speech_window(clip)
    if s1 is None:
        return total, "no speech detected - kept whole clip"

    note = ""
    if line:
        le, extra = line_end(clip, line)
        if le is not None:
            if extra:
                note = "; dropped trailing \"%s\"" % extra[:40]
            s1 = le                      # the SCRIPTED line, not the last noise
        else:
            # Loud, because the fallback cuts LATER: the energy detector counts any
            # vocalisation, so the clip keeps a drifting tail. Episode 4 shipped a 5.38s
            # hook this way when the good frames ended at 4.75s.
            note = ("; WARNING transcription unavailable -- fell back to the energy "
                    "detector, which cuts late. Check the last frame by eye")
    floor = min(total, s1 + pad)
    if audio_only:
        return floor, "audio-only: line ends %.2fs, +%.2fs pad%s" % (s1, pad, note)

    F, W, H = frames_gray(clip, 0.0)
    if F is None:
        return floor, "could not read frames - cut at speech end + pad"
    fps = 12.0
    base_i = int(((s0 + (s1 - s0) * 0.6)) * fps)
    base_i = max(0, min(base_i, len(F) - 1))
    base = F[base_i]

    # what "normal" looks like while he is still speaking
    in_speech = [face_psnr(F[i], base) for i in range(base_i, min(len(F), int(s1 * fps)))]
    ref_psnr = float(np.median(in_speech)) if in_speech else 99.0

    start_i = int(floor * fps)
    run = 0
    for i in range(start_i, len(F)):
        d = displacement(F[i], base, W, H)
        p = face_psnr(F[i], base)
        run = run + 1 if d > DRIFT_PX else 0
        if (ref_psnr - p) > PSNR_DROP:
            # Cut BEFORE the change, not at it. Cutting at frame i keeps every frame up to
            # i, which includes the start of whatever went wrong -- on episode 5's hook the
            # face changed at 5.08s and cutting there kept a second of him looking down and
            # starting to speak again, which the operator caught by eye. Backing off
            # CHANGE_MARGIN lands at 4.78s, against 4.75s chosen by eye (#56).
            t = max(floor, (i / fps) - CHANGE_MARGIN)
            t, eye = back_off_to_open_eyes(F, fps, t, min(floor, t))
            return t, ("line ends %.2fs; cut at %.2fs (face changed %.1f dB)%s%s"
                       % (s1, t, ref_psnr - p, note, eye))
        if run >= DRIFT_WIN:
            t = max(floor, (i - DRIFT_WIN + 1) / fps)
            t, eye = back_off_to_open_eyes(F, fps, t, min(floor, t))
            return t, ("line ends %.2fs; cut at %.2fs (framing %.0fpx off)%s%s"
                       % (s1, t, d, note, eye))
    t, eye = back_off_to_open_eyes(F, fps, floor, min(floor, s1))
    return t, "line ends %.2fs; cut at %.2fs%s%s" % (s1, t, note, eye)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--pad", type=float, default=0.25,
                    help="seconds of hold to keep after the last word (default 0.25)")
    ap.add_argument("--line", default="",
                    help="the exact scripted line. Sets the cut FLOOR at the last scripted "
                         "word, so anything the model said afterwards is dropped. From "
                         "there the picture checks hold the shot only while it still looks "
                         "like him looking at camera, and cut at the first frame it does "
                         "not -- gaze off the lens, a grimace, a half-blink, framing "
                         "drift. Strongly recommended")
    ap.add_argument("--audio-only", action="store_true",
                    help="VO clips: cut on speech end alone, never inspect the picture")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = []
    for p in args.clips:
        files += sorted(p.glob("*.mp4")) if p.is_dir() else ([p] if p.exists() else [])
    if not files:
        sys.exit("ERROR: no clips found.")

    print("%-26s %-8s %-8s %s" % ("clip", "was", "now", "reason"))
    for c in files:
        t, why = find_cut(c, args.pad, args.audio_only, args.line)
        total = dur(c)
        print("%-26s %-8.2f %-8.2f %s" % (c.name, total, t, why))
        if args.dry_run:
            continue
        out_dir = args.out_dir or c.parent / "trimmed"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / c.name
        sh(["ffmpeg", "-v", "error", "-i", str(c), "-t", "%.3f" % t,
            "-c:v", "libx264", "-crf", "17", "-preset", "slow",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(out), "-y"])
    if not args.dry_run:
        print("\nTrimmed clips in %s" % (args.out_dir or "each clip's ./trimmed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
