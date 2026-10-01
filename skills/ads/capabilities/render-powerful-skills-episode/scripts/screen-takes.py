#!/usr/bin/env python3
"""Screen generated character takes against every acceptance criterion at once.

WHY THIS EXISTS
Episode 1 cost far more round trips than it should have, because the acceptance criteria
were discovered one at a time -- each one found by watching a clip, reporting a defect,
and regenerating. Six criteria, six round trips. They are all measurable, so none of them
should ever again be found by eye:

    1. dialogue    the exact line, no invented words   (Veo drops words in long prompts)
    2. voice       matches the locked character voice  (no audio reference on Veo)
    3. music       bed level after separation           (varies 36 dB by seed)
    4. drift       framing at the end vs the start      ("gentle drift" accumulates)
    5. tail        silence after the line               (needed for a post-line last frame)
    6. length      speech fits before the degradation zone (~80% through)

Run this on a batch, watch only the takes that pass, pick by eye. The one thing that
CANNOT be automated is whether the performance is any good -- that is the whole point of
generating several and choosing.

Usage:
    screen-takes.py DIR_OR_CLIPS... --line "<the exact line>" [--target-f0 153.8]
                                    [--need-tail] [--json out.json]

Needs ffmpeg, numpy, demucs, and FAL_KEY (for transcription only; pass --no-asr to skip).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

NULL = "NUL" if os.name == "nt" else "/dev/null"
FPS = 30.0                      # reporting timeline


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def audio(clip: Path, sr=16000):
    import numpy as np
    tmp = Path(tempfile.mkdtemp(prefix="scr_"))
    try:
        w = tmp / "a.wav"
        sh(["ffmpeg", "-v", "error", "-i", str(clip), "-vn", "-ac", "1",
            "-ar", str(sr), str(w), "-y"])
        if not w.exists():
            return None, sr
        with wave.open(str(w)) as f:
            sr = f.getframerate()
            x = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
        return x.astype(float) / 32768.0, sr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def speech_window(x, sr):
    """Start and end of speech, from the voice-band envelope."""
    import numpy as np
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


def f0_median(x, sr, sub=0.80):
    """Median F0 by autocorrelation, with an OCTAVE-SAFE lag choice.

    The plain argmax doubles on some frames -- a periodic waveform correlates nearly as well
    at half its period, and whichever peak wins is a coin toss that depends on the harmonic
    balance of that frame. On episode 4's VO A, 26% of frames landed above 200 Hz against
    VO B's 5%, dragging the median to 161.6 Hz for a line whose bulk sat at 120-140 like
    every other take. Three separate pitch corrections then fired to "fix" a voice that was
    never sharp, and the compounded shift is what made it sound robotic (#48).

    So: among lags correlating within `sub` of the best, take the LONGEST -- the lowest
    frequency, which is the fundamental rather than one of its harmonics.
    """
    import numpy as np
    win, hop = int(0.04 * sr), int(0.01 * sr)
    lo, hi = int(sr / 300), int(sr / 70)
    vals = []
    for i in range(0, max(0, len(x) - win), hop):
        s = x[i:i + win]
        if np.sqrt(np.mean(s ** 2)) < 0.02:
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
        if r[k] < 0.35:
            continue
        for mlt in (2, 3):
            if k * mlt < hi and r[k * mlt] >= sub * r[k]:
                k = k * mlt
        vals.append(sr / k)
    return round(float(np.median(vals)), 1) if len(vals) >= 10 else None


def bed_level(clip: Path, passes: int = 2) -> float | None:
    """Isolated non-voice content, via Demucs. This is the music screen.

    Measured more than once and reported as the LOUDEST reading, because the separation
    is not deterministic: the same take measured -62.0 dB and -57.5 dB on two sequential
    runs of this script, a 4.5 dB swing on a screen whose floor is -50. One pass can
    therefore let a take with an audible bed through, and a false pass ships. A false
    FAIL costs nothing -- the pipeline separates a bed-only failure and re-screens it
    (#124) -- so the conservative reading is the right one to keep (#131).
    """
    reads = [_bed_once(clip) for _ in range(max(1, passes))]
    reads = [r for r in reads if r is not None]
    return max(reads) if reads else None


def _bed_once(clip: Path) -> float | None:
    import numpy as np
    tmp = Path(tempfile.mkdtemp(prefix="scrbed_"))
    try:
        w = tmp / "in.wav"
        sh(["ffmpeg", "-v", "error", "-i", str(clip), "-vn", "-ac", "2",
            "-ar", "44100", str(w), "-y"])
        if not w.exists():
            return None
        sh([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
            "-o", str(tmp / "s"), str(w)])
        bed = tmp / "s" / "htdemucs" / "in" / "no_vocals.wav"
        if not bed.exists():
            return None
        with wave.open(str(bed)) as f:
            ch = f.getnchannels()
            y = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
        y = y.astype(float) / 32768.0
        if ch == 2:
            y = y.reshape(-1, 2).mean(axis=1)
        return round(float(20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-12)), 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def framing_drift(clip: Path, until: float | None = None) -> float | None:
    """How far the framing has moved by the end, in px on a 1080-wide frame.

    Frame-to-frame motion misses this entirely -- a slow one-way drift is small every
    frame and large overall. Measured first frame against last.

    MEASURE ONLY THE PART THAT SHIPS. Drift accumulates with time, and the tail after the
    scripted line is cut away, so measuring the full take rejects clips for footage nobody
    sees. Episode 5's closer seed851 measured 30.3 px over its full 6s and 5.0 px over the
    3.70s actually kept -- rejected for 2.3 seconds that get thrown away, while carrying
    the best settle tail in the series (2.72s) and a passing voice. `subject_drift` already
    took `until` for exactly this reason; this function not doing so was an inconsistency,
    not a decision (#52).
    """
    import numpy as np
    tmp = Path(tempfile.mkdtemp(prefix="scrdr_"))
    try:
        a, b = tmp / "a.png", tmp / "b.png"
        sh(["ffmpeg", "-v", "error", "-i", str(clip), "-frames:v", "1",
            "-update", "1", "-vf", "scale=480:-2,format=gray", str(a), "-y"])
        if until is not None and until > 0.3:
            sh(["ffmpeg", "-v", "error", "-ss", "%.3f" % max(0.0, until - 0.05),
                "-i", str(clip), "-frames:v", "1", "-update", "1",
                "-vf", "scale=480:-2,format=gray", str(b), "-y"])
        else:
            sh(["ffmpeg", "-v", "error", "-sseof", "-0.08", "-i", str(clip), "-frames:v", "1",
                "-update", "1", "-vf", "scale=480:-2,format=gray", str(b), "-y"])
        if not (a.exists() and b.exists()):
            return None
        arrs = []
        for p in (a, b):
            r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"], capture_output=True)
            arrs.append(np.frombuffer(r.stdout, dtype=np.uint8).astype(float))
        n = min(len(arrs[0]), len(arrs[1]))
        h = int(n / 480)
        A = arrs[0][:h * 480].reshape(h, 480)
        B = arrs[1][:h * 480].reshape(h, 480)
        wy, wx = np.hanning(h)[:, None], np.hanning(480)[None, :]
        fa, fb = np.fft.rfft2(A * wy * wx), np.fft.rfft2(B * wy * wx)
        cs = fa * np.conj(fb)
        cs /= np.abs(cs) + 1e-9
        c = np.fft.irfft2(cs, s=A.shape)
        iy, ix = np.unravel_index(np.argmax(c), c.shape)
        dy = iy - h if iy > h // 2 else iy
        dx = ix - 480 if ix > 240 else ix
        return round(float(np.hypot(dx, dy)) * (1080.0 / 480), 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def subject_drift(clip: Path, until: float | None = None):
    """How much the SUBJECT grows in frame, as a percentage, up to `until` seconds.

    This is the drift mode that actually ships, and `framing_drift` above cannot see it.
    That one is a phase correlation, so it measures TRANSLATION -- and the failure here is
    a push-in with almost no translation: measured on episode 2's closer, the whole frame
    scaled -1.0% while the head grew 5.3%. He leans toward the lens; the camera barely
    moves. Phase correlation reports "no drift" and the take passes.

    Measured on 17 raw takes across both episodes, EVERY ONE peaked between +8.4% and
    +16.7% -- despite the prompt stating "he stays the same size in frame throughout" and
    "never gradually pulling back or pushing in". The instruction does not work and no seed
    escapes it (critical knowledge #36), so this is a ranking signal, not a pass/fail
    ideal: pick the take that drifts least over the part you will KEEP.

    Pass `until` = the end of the line. Drift after that is trimmed away and must not count
    against the take -- scoring the whole 6s rejects good takes for a tail nobody sees.

    Returns (peak_pct, end_pct) or None. Relies on the locked character's dark hair against
    a light wall, which is fixed for this series by definition (#0).
    """
    import numpy as np
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(clip)]).stdout.strip()
    try:
        dur = float(r)
    except ValueError:
        return None
    end = min(dur - 0.10, until) if until else dur - 0.10
    if end <= 0.2:
        return None
    widths = []
    for i in range(9):
        t_ = 0.05 + (end - 0.05) * i / 8.0
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t_, "-i", str(clip),
                              "-frames:v", "1", "-vf", "scale=1080:-2,format=gray",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True).stdout
        a = np.frombuffer(out, dtype=np.uint8).astype(float)
        h = int(len(a) / 1080)
        if h < 900:
            continue
        g = a[:h * 1080].reshape(h, 1080)
        band = g[int(h * 0.27):int(h * 0.47), 400:1080] < 70
        cs = np.nonzero(band.sum(0) > 8)[0]
        if len(cs) >= 2:
            widths.append(float(cs.max() - cs.min()))
    if len(widths) < 5:
        return None
    b = widths[0]
    return (round(100 * (max(widths) / b - 1), 1), round(100 * (widths[-1] / b - 1), 1))


def handheld(clip: Path) -> float | None:
    """MEDIAN frame-to-frame camera motion -- does the camera move on a typical frame?

    The prompt asks for it, but Veo delivers it only sometimes, and it is a per-seed
    lottery exactly like the music bed. Measured across three batches: episode 2's closer
    batch had 8 of 12 takes above 0.8 px, while episode 3's hook batch had 1 of 12 and a
    median of 0.04 -- effectively locked off. Episode 3 shipped a static hook and closer
    because voice, dialogue and drift all passed and NOTHING was looking at shake.

    A tripod-still selfie reads as fake, so this is a real acceptance criterion.

    This reports the MEDIAN, not the p90, and the difference is not academic. A quantised
    or barely-moving clip produces occasional big jolts -- a healthy p90 -- while sitting
    frozen the rest of the time. Episodes 3 and 4 both cleared a 0.8 p90 bar at medians of
    0.02-0.27 and read as locked off. Shipped handheld references: episode 1's hook is
    median 0.70, episode 2's is 0.87; a tripod is 0.02-0.07 (#49).
    """
    import importlib.util as u
    spec = u.spec_from_file_location("ab", Path(__file__).resolve().parent / "ab_report.py")
    ab = u.module_from_spec(spec)
    spec.loader.exec_module(ab)
    try:
        return ab.camera_motion(clip).get("median")
    except Exception:
        return None


def transcribe(clip: Path) -> str | None:
    """scribe-v2 needs extracted audio; it rejects mp4 containers."""
    if "FAL_KEY" not in os.environ and "FAL_API_KEY" in os.environ:
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
    if not os.environ.get("FAL_KEY"):
        return None
    try:
        from fal_client import subscribe, upload_file
    except ImportError:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="scrasr_"))
    try:
        m = tmp / "a.mp3"
        sh(["ffmpeg", "-v", "error", "-i", str(clip), "-vn", "-ac", "1",
            "-ar", "44100", str(m), "-y"])
        if not m.exists():
            return None
        r = subscribe("fal-ai/elevenlabs/speech-to-text/scribe-v2",
                      arguments={"audio_url": upload_file(str(m))}, with_logs=False)
        return ((r or {}).get("text") or "").strip()
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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


def norm(s: str) -> str:
    """Normalise for comparison, including spelling numbers out.

    A transcript says "one hundred" where the line reads "100". Literal matching rejected
    five of six good VO takes on exactly that, so numerals are expanded on both sides.
    "a hundred" is also accepted for "one hundred", which is how it is usually spoken.
    """
    t = (s or "").lower()
    t = re.sub(r"\d+", lambda m: " " + _spell(int(m.group())) + " ", t)
    t = re.sub(r"\ba hundred\b", "one hundred", t)
    t = re.sub(r"\ba thousand\b", "one thousand", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def screen(clip: Path, args) -> dict:
    # `path` as well as `name`, so the --json output is self-describing and
    # build-review-reel.py can find the file without being told the directory.
    r = {"clip": clip.name, "path": str(clip)}
    x, sr = audio(clip)
    dur = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1", str(clip)]).stdout.strip() or 0)
    r["duration"] = round(dur, 2)
    fails = []

    if x is not None:
        s0, s1 = speech_window(x, sr)
        r["speech"] = (round(s0, 2), round(s1, 2)) if s0 is not None else None
        r["tail"] = round(dur - s1, 2) if s1 is not None else None
        r["f0"] = f0_median(x, sr)
        if args.target_f0 and r["f0"]:
            r["f0_delta"] = round(r["f0"] - args.target_f0, 1)
            if abs(r["f0_delta"]) > args.f0_tolerance:
                fails.append("voice %+.1fHz" % r["f0_delta"])
        if args.need_tail and (r["tail"] or 0) < args.min_tail:
            fails.append("tail %.2fs" % (r["tail"] or 0))

    # Defined here, not below: BOTH drift measures need the kept-portion boundary (#52).
    #
    # Prefer the SCRIPTED-LINE end from trim-tail's ASR matcher over the energy window.
    # The energy detector over-reports -- it counts breaths and trailing vocalisation as
    # speech, measured 0.5-0.6s late on episodes 4 and 5 -- and here that difference
    # decided a take: episode 6's closer runs its scripted line to 3.70s and ships 4.33s,
    # but the energy window said 5.98s, so drift was measured across 1.6s of footage that
    # gets cut and the take was rejected at 25.5px when the shipped portion is 9.0 (#60).
    line_end = r["speech"][1] if r.get("speech") else None
    if args.line:
        try:
            import importlib.util as _u
            _sp = _u.spec_from_file_location("tt", Path(__file__).resolve().parent
                                             / "trim-tail.py")
            _tt = _u.module_from_spec(_sp)
            _sp.loader.exec_module(_tt)
            _le = _tt.line_end(clip, args.line)
            if isinstance(_le, (tuple, list)):
                _le = _le[0]
            if _le and 0.5 < float(_le) < (line_end or 1e9):
                line_end = float(_le)
        except Exception:
            pass
    r["drift"] = framing_drift(clip, until=line_end)
    if r["drift"] is not None and r["drift"] > args.max_drift:
        fails.append("drift %.0fpx" % r["drift"])

    # Push-in, over the part that survives the trim. framing_drift is translation-only and
    # cannot see this (#36).
    r["shake"] = handheld(clip)
    if r["shake"] is not None and r["shake"] < args.min_shake:
        fails.append("no shake %.2fpx" % r["shake"])

    sd = subject_drift(clip, until=line_end)
    r["zoom"] = sd
    if (args.max_subject_drift is not None and sd is not None
            and sd[0] > args.max_subject_drift):
        fails.append("push-in %+.1f%%" % sd[0])

    # EXPENSIVE CHECKS LAST, and only on takes still in the running. Demucs separation and
    # the ASR call dominate the wall clock, and ASR is billed per clip -- running them on a
    # take already rejected for voice buys nothing. On episode 3's batches roughly half the
    # takes failed on voice or drift alone, so this halves both the time and the ASR spend.
    if not fails:
        r["bed"] = bed_level(clip, args.bed_passes) if not args.no_bed else None
        if r["bed"] is not None and r["bed"] > args.max_bed:
            fails.append("music %.1fdB" % r["bed"])

    if not fails and not args.no_asr and args.line:
        t = transcribe(clip)
        r["transcript"] = t
        if t is None:
            # A take that could not be transcribed has NOT passed the dialogue check --
            # it was never checked. Treating that as a pass is how episode 4 screened a
            # whole batch with the dialogue criterion silently switched off, after the fal
            # balance ran out and every upload started returning 403. Say so instead.
            fails.append("dialogue UNCHECKED (transcription unavailable)")
        else:
            want, got = norm(args.line), norm(t)
            # the take may be trimmed mid-line; require it to be a clean prefix
            if not (got.startswith(want) or want.startswith(got)):
                fails.append("dialogue")

    r["fails"] = fails
    r["pass"] = not fails
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="clips, or a directory of them")
    ap.add_argument("--line", help="the exact dialogue line, for the transcript check")
    ap.add_argument("--target-voice", type=Path,
                    help="locked character voice file. The target is MEASURED from it with "
                         "the identical code path used on the takes -- never hand-enter a "
                         "number from another tool, the windows differ and the values are "
                         "not comparable (this bit once and rejected a shipped take)")
    ap.add_argument("--target-f0", type=float,
                    help="explicit target in Hz. Prefer --target-voice")
    ap.add_argument("--f0-tolerance", type=float, default=8.0)
    ap.add_argument("--max-bed", type=float, default=-50.0,
                    help="reject takes whose isolated bed is louder than this (default -50)")
    ap.add_argument("--min-shake", type=float, default=0.45,
                    help="reject takes with less handheld motion than this (MEDIAN px). A "
                         "locked-off selfie reads as fake, and Veo delivers shake only on "
                         "some seeds")
    ap.add_argument("--max-subject-drift", type=float, default=None,
                    help="optional hard cap on push-in %%. OFF by default and it should "
                         "usually stay off: every take measured so far peaks 8.4-17.2%%, "
                         "including the ones that shipped, so any threshold in that range "
                         "rejects good takes. The column RANKS takes; use it to pick the "
                         "flattest, not to find a clean one (#36)")
    ap.add_argument("--max-drift", type=float, default=25.0,
                    help="reject if the framing moved more than this by the last frame")
    ap.add_argument("--need-tail", action="store_true",
                    help="require silence after the line, for a post-dialogue last frame")
    ap.add_argument("--min-tail", type=float, default=0.8)
    ap.add_argument("--no-asr", action="store_true", help="skip transcription")
    ap.add_argument("--no-bed", action="store_true", help="skip the music screen (slow)")
    ap.add_argument("--bed-passes", type=int, default=2,
                    help="Demucs readings per take, reported as the loudest "
                         "(default 2). The separation is not deterministic: the "
                         "same take read -62.0 and -57.5 dB on two runs.")
    ap.add_argument("--jobs", type=int, default=4,
                    help="takes screened at once (default 4). The Demucs bed pass "
                         "is the slow part and each take is independent.")
    ap.add_argument("--json", help="write full results here")
    args = ap.parse_args()

    if args.target_voice:
        if not args.target_voice.exists():
            sys.exit("ERROR: no such voice file: %s" % args.target_voice)
        vx, vsr = audio(args.target_voice)
        if vx is None:
            sys.exit("ERROR: could not read the target voice file.")
        args.target_f0 = f0_median(vx, vsr)
        if not args.target_f0:
            sys.exit("ERROR: could not measure a fundamental in the target voice file.")
        print("target voice %s -> %.1f Hz (+/-%.0f)"
              % (args.target_voice.name, args.target_f0, args.f0_tolerance),
              file=sys.stderr)

    clips = []
    for p in args.paths:
        if p.is_dir():
            clips += sorted(q for q in p.glob("*.mp4"))
        elif p.exists():
            clips.append(p)
    if not clips:
        sys.exit("ERROR: no clips found.")

    # Screened in parallel. Each take is independent and the work is CPU-bound on the
    # Demucs pass that measures the music bed, roughly 40 seconds a clip: eight takes in
    # sequence came to 5.2 minutes of a 13 minute finish, for no reason other than the
    # loop. Order is preserved so the table still reads by seed.
    jobs = max(1, min(args.jobs, len(clips)))
    if jobs > 1:
        from concurrent.futures import ThreadPoolExecutor
        print("screening %d takes, %d at a time ..." % (len(clips), jobs), file=sys.stderr)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            rows = list(pool.map(lambda c: screen(c, args), clips))
    else:
        rows = []
        for c in clips:
            print("screening %s ..." % c.name, file=sys.stderr)
            rows.append(screen(c, args))

    print("\n| take | speech | tail | F0 | bed | drift | push-in | verdict |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        sp = "%.2f-%.2f" % r["speech"] if r.get("speech") else "-"
        f0 = ("%.1f (%+.1f)" % (r["f0"], r["f0_delta"])
              if r.get("f0") and "f0_delta" in r else (str(r.get("f0") or "-")))
        z = "%+.1f%%" % r["zoom"][0] if r.get("zoom") else "-"
        print("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["clip"], sp, r.get("tail", "-"), f0, r.get("bed", "-"), r.get("drift", "-"),
            z, r.get("shake", "-"), "**PASS**" if r["pass"] else ", ".join(r["fails"])))

    zs = [(r["zoom"][0], r["clip"]) for r in rows if r.get("zoom")]
    if zs:
        zs.sort()
        print("\npush-in over the kept portion, flattest first: %s"
              % ", ".join("%s %+.1f%%" % (c, z) for z, c in zs[:4]))
        print("Every take drifts (#36) -- this ranks them, it does not gate them.")

    ok = [r for r in rows if r["pass"]]
    print("\n%d of %d takes pass. Watch those; pick on performance." % (len(ok), len(rows)))
    if not ok:
        print("None passed -- generate more seeds rather than trying to repair one.")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
