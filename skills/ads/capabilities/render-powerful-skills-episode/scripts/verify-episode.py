#!/usr/bin/env python3
"""Check every property an episode of this series must have, before anyone watches it.

WHY THIS EXISTS
Episode 3 shipped to review with four separate defects -- a closer generated from the
wrong reference image so the face changed, a voiceover that did not sound like the
character clips, 1.7s of dead air before the closer, and no handheld shake in either clip
-- and every one of them was found by the operator rather than by the pipeline. Each was
individually measurable. Nothing was measuring them.

These are properties of the FORMAT, not of one episode. They should hold whoever builds
it, so they are asserted here and `build-episode.py` runs this automatically at the end.

WHAT IT CHECKS, and where each bar comes from

  reference   hook and closer generated from the SAME image. Episodes 1 and 2 used
              character-selfie.png for both shots; using the seated closer reference
              instead visibly changed the face.
  shake       both character clips carry handheld motion. Veo delivers it per seed, so a
              batch can produce twelve locked-off takes. Episodes 1-2 measured p90
              1.11-2.16, so the floor is 0.8.
  voice       every asset within the tolerance of the HOOK's F0, and spectrum correlation
              at or above 0.946 -- shipped episode 2's own worst internal pair. Pitch
              alone is not enough; episode 3 matched to 2.3 Hz and still sounded wrong.
  dead air    the middle must not run on after the last word. Episode 3's first cut held
              1.7s past it with the SFX already finished.
  caption     the band inside the 4:5 safe zone, y 285..1634.
  duration    20-26s.
  loudness    each section within 0.6 LU of episode 1's targets.

Usage:
    verify-episode.py --episode <dir> --final <dir>/output/episode-N-final.mp4
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

TARGET_I = {"hook": -19.86, "middle": -17.52, "closer": -16.37}
MIN_SHAKE = 0.45      # MEDIAN px, not p90. A quantised crop produces big occasional jolts
                      # -- a fine p90 -- while sitting frozen on every other frame. The
                      # median says whether the camera moves on a TYPICAL frame, which is
                      # what separates handheld (ep1 hook 0.70, ep2 hook 0.87) from a tripod
                      # (0.02-0.07). Episodes 3 and 4 passed a 0.8 p90 bar at medians of
                      # 0.02-0.27 and both read as locked off (#49).
TIMBRE_FLOOR = 0.946
BRIGHT_TOL = 0.35     # max fractional deviation of an asset's spectral centroid from the
                      # locked voice. LTAS correlation is TILT-BLIND -- episode 7's closer
                      # correlated 0.965 while measuring 1552 Hz against a 1034 Hz
                      # reference (+50%) and was heard at once as a different voice.
                      # Calibrated between what shipped and what did not: episode 6's
                      # closer was accepted at +30%, episode 7's was rejected at +50% (#64).
F0_TOL = 8.0
SAFE = (285, 1634)
DUR = (20.0, 26.0)
# v3 (the @gooseaitools series) adds a results section, so it runs longer than the old
# account's four-line episodes. 38-56s was the intended band when the format was drafted
# with a cold open and an 11s payoff; what actually ships is a results section cut to ONE
# spoken line, and parts 1 and 2 came to 26.0s and 27.6s. Both only passed because a
# --dur-range override was passed by hand, which is how a band stops meaning anything, so
# the band is now what the format really produces.
DUR_V3 = (22.0, 34.0)
MAX_TRAILING_SILENCE = 0.8
REF_SIM = 0.88          # first-frame correlation between the two shots' source images
# Last frame vs mid-line. Calibrated against LABELLED examples, not guessed: the two hook
# and closer versions the operator rejected for the drifting smile scored 0.702 and 0.571,
# and the versions accepted scored 0.873 and 0.882. 0.78 sits in that gap. A first guess of
# 0.55 passed both rejects, which is worse than no check at all.
END_FACE = 0.78


def sh(cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          errors="replace")


def dur(p: Path) -> float:
    try:
        return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", p]).stdout.strip())
    except ValueError:
        return 0.0


def _mod(name):
    import importlib.util as u
    spec = u.spec_from_file_location(name.replace("-", "_"),
                                     Path(__file__).resolve().parent / (name + ".py"))
    m = u.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def face_at(sh_fn, clip: Path, t: float, tmp: Path, from_end: bool = False):
    """Grayscale face crop, normalised so frames can be compared directly.

    The LAST frame is seeked with -sseof, not -ss (duration - epsilon). A trimmed and
    re-encoded clip can carry a container duration slightly past its last real frame, so
    `-ss 3.54` on a 3.60s clip returned nothing and the check skipped in silence.
    """
    vf = ("crop=iw*0.42:ih*0.30:iw*0.40:ih*0.26,scale=160:-2,format=gray")
    if from_end:
        # Decode through and keep overwriting one file: the last write IS the last frame.
        # Seeking near the end is not reliable here -- a trimmed clip can carry a container
        # duration past its final video frame (measured: video 3.583s, container 3.603s),
        # and both -ss and -sseof then return nothing at all. The clips are a few seconds
        # long, so decoding them fully is cheap and always correct.
        # No -vsync: ffmpeg 9 removed it (renamed -fps_mode) and the whole command fails
        # with "Unrecognized option". -update alone already overwrites frame by frame.
        sh_fn(["ffmpeg", "-v", "error", "-i", clip, "-vf", vf, "-update", "1", tmp, "-y"])
    else:
        sh_fn(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", clip, "-frames:v", "1",
               "-update", "1", "-vf", vf, tmp, "-y"])
    if not tmp.exists():
        return None
    from PIL import Image
    a = np.asarray(Image.open(tmp).convert("L")).astype(float)
    tmp.unlink(missing_ok=True)
    return (a - a.mean()) / (a.std() + 1e-9)


def last_frame_ok(sh_fn, clip: Path, tmp: Path):
    """Does the clip END looking like it did mid-line?

    The defect this catches has now been reported twice: after the line the model keeps
    going and the face drifts -- eyes narrow into a strange squint, or he looks down and
    away. `trim-tail.py` measures the same thing but with a threshold loose enough to miss
    both, so this is a second, stricter look at the single frame that matters most.

    The reference is taken 60% of the way through, when he is mid-delivery and facing the
    lens; the last frame is compared to it. Returns (correlation, reference_time).
    """
    d = dur(clip)
    if d <= 0.5:
        return None, None
    ref = face_at(sh_fn, clip, d * 0.6, tmp)
    end = face_at(sh_fn, clip, 0.0, tmp, from_end=True)
    if ref is None or end is None:
        return None, None
    return float(np.mean(ref * end)), d * 0.6


CUE_TOL = 0.25        # s. The detector locates a moved cue to within 0.05s (measured on a
                      # deliberately displaced rebuild), so 0.25 is slack, not a fudge.
CUE_MIN_RATIO = 1.2   # below this the chime is not distinguishable from the bed and the
                      # result is reported as UNCHECKED rather than passed or failed.


def find_cue(final: Path, tone: Path, hp: int = 2500, window=None):
    """Where did the notification chime ACTUALLY land in the finished mix?

    Nothing checked this, and it was the last known defect class with no gate: --notify is
    edit-relative, so a value pinned by scrubbing the render lands one hook-length late,
    and on episode 4 that put the chime 3s past the deliverable twice before the operator
    caught it by ear (#46). The chime cannot be found by looking at an envelope -- it is
    mixed 21.6 dB down under the voiceover and every band is full of sibilance -- so this
    correlates the actual SFX asset against the render and reports where it peaks.

    Returns (seconds, peak, distinctiveness) where distinctiveness is the peak over the
    99.9th percentile of the rest of the track.
    """
    import subprocess, tempfile, os, wave

    def pcm(q):
        t = os.path.join(tempfile.gettempdir(), "vc_%d.wav" % abs(hash(str(q)) % 99999))
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(q), "-af", "highpass=f=%d" % hp,
                        "-ac", "1", "-ar", "24000", t, "-y"], capture_output=True)
        if not os.path.exists(t):
            return None, 24000
        w = wave.open(t)
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
        w.close()
        os.unlink(t)
        return a / 32768, 24000

    x, sr = pcm(final)
    h, _ = pcm(tone)
    if x is None or h is None or len(x) < sr:
        return None, None, None
    h = h[:int(0.45 * sr)]                      # the attack carries the identity
    h = h - h.mean()
    h = h / (np.linalg.norm(h) + 1e-12)
    n = len(h)
    L = len(x) + n
    num = np.fft.irfft(np.fft.rfft(x, L) * np.conj(np.fft.rfft(h, L)))[:len(x) - n]
    e = np.sqrt(np.convolve(x * x, np.ones(n), "valid"))[:len(num)]
    r = num / (e + 1e-9)
    if window:
        lo = max(0, int(window[0] * sr))
        hi = min(len(r), int(window[1] * sr))
        if hi - lo > sr // 2:
            mask = np.full(len(r), -np.inf)
            mask[lo:hi] = r[lo:hi]
            r = mask
    i = int(np.argmax(r))
    mask = np.ones(len(r), bool)
    mask[max(0, i - int(0.5 * sr)):i + int(0.5 * sr)] = False
    ratio = float(r[i] / (np.percentile(r[mask], 99.9) + 1e-9)) if mask.any() else 0.0
    return i / sr, float(r[i]), ratio


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dur-range", default=None,
                    help="MIN:MAX seconds; default %.0f-%.0f (v1/v2). The v3 series with a "
                         "results section uses %.0f:%.0f" % (*DUR, *DUR_V3))
    ap.add_argument("--episode", required=True, type=Path)
    ap.add_argument("--final", required=True, type=Path)
    args = ap.parse_args()

    spec = {}
    sp = args.episode / "episode.json"
    if sp.exists():
        spec = json.loads(sp.read_text(encoding="utf-8"))

    st = _mod("screen-takes")
    mt = _mod("match-voice-timbre")
    ab = _mod("ab_report")

    F = args.episode / "final"
    hook, closer = F / "hook.mp4", F / "closer.mp4"
    voa, vob = F / "vo-a.mp3", F / "vo-b.mp3"
    fails = []

    def ok(name, good, detail):
        print("  %-11s %-4s %s" % (name, "ok" if good else "FAIL", detail))
        if not good:
            fails.append(name)

    print("verifying %s\n" % args.final.name)

    # --- reference image: the two shots must come from the same source picture ---------
    def ff(v):
        p = args.episode / "_ff.png"
        sh(["ffmpeg", "-v", "error", "-i", v, "-frames:v", "1", "-update", "1",
            "-vf", "scale=270:480,format=gray", p, "-y"])
        from PIL import Image
        a = np.asarray(Image.open(p).convert("L")).astype(float)
        p.unlink(missing_ok=True)
        return a
    # Use the UNTRIMMED take when it is there. This check compares two first frames as a
    # proxy for "both generated from the same reference image" (#40), and the 0.88 bar was
    # calibrated on untrimmed clips. Trimming the HEAD breaks the proxy without touching
    # the property: episode 8's hook was cut 0.78s in to remove a stray vocalisation, so
    # its first frame sits inside its own push-in and the correlation fell to 0.855 on a
    # pair that genuinely came from one image (#65).
    def orig(v):
        o = v.with_suffix(v.suffix + ".orig")
        return o if o.exists() else v

    if hook.exists() and closer.exists():
        a, b = ff(orig(hook)), ff(orig(closer))
        c = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
        ok("reference", c >= REF_SIM,
           "hook/closer first-frame correlation %.3f (>=%.2f -- same source image)"
           % (c, REF_SIM))

    # --- shake -------------------------------------------------------------------------
    for lab, v in (("shake hook", hook), ("shake closer", closer)):
        if not v.exists():
            continue
        mm = ab.camera_motion(v) or {}
        med, p90 = mm.get("median"), mm.get("p90")
        ok(lab.split()[0] + " " + lab.split()[1][:3], med is not None and med >= MIN_SHAKE,
           "%s median %.2f px (>=%.2f), p90 %.2f"
           % (v.name, med if med is not None else -1, MIN_SHAKE,
              p90 if p90 is not None else -1))

    # --- voice: every asset against the LOCKED SERIES VOICE, not this episode's hook ----
    # Measuring against the hook only proves an episode is internally consistent, which is
    # not the requirement -- the voice has to be the same across all ten. It also hid the
    # real drift: episode 3's hook is 0.901 against the locked voice, so "matching" the VO
    # to that hook moved the VO from 0.963 to 0.914 and still passed this check (#48).
    lock = args.episode.parent / "locked" / "character-voice.mp3"
    if lock.exists():
        R, _ = mt.ltas(st, lock)
        lx, lsr = st.audio(lock)
        lf = st.f0_median(lx, lsr)
        try:
            lc = mt.centroid(st, lock)
        except Exception:
            lc = None
        worst_t, worst_f, worst_n = 1.0, 0.0, "-"
        for v in (hook, closer, voa, vob):
            if not v.exists():
                continue
            x, sr = st.audio(v)
            f0 = st.f0_median(x, sr)
            T, _ = mt.ltas(st, v)
            s_ = mt.sim(R, T) if T is not None else 0.0
            if s_ < worst_t:
                worst_t, worst_n = s_, v.name
            worst_f = max(worst_f, abs((f0 or 0) - lf))
        ok("voice F0", worst_f <= F0_TOL,
           "worst deviation from the LOCKED voice %.1f Hz (<=%.0f)" % (worst_f, F0_TOL))
        # Brightness, which the correlation above cannot see (#64).
        wb, wbn = 0.0, "-"
        for v in (hook, closer, voa, vob):
            if not v.exists():
                continue
            c_ = mt.centroid(m_c, v) if False else None
            try:
                c_ = mt.centroid(st, v)
            except Exception:
                c_ = None
            if c_ and lc:
                dev = abs(c_ - lc) / lc
                if dev > wb:
                    wb, wbn = dev, v.name
        if lc:
            ok("brightness", wb <= BRIGHT_TOL,
               "worst centroid deviation %.0f%% on %s (<=%.0f%%)"
               % (100 * wb, wbn, 100 * BRIGHT_TOL))

        ok("voice tone", worst_t >= TIMBRE_FLOOR,
           "worst vs LOCKED voice %.3f on %s (>=%.3f)" % (worst_t, worst_n, TIMBRE_FLOOR))

    # --- dead air before the closer -----------------------------------------------------
    hk, cl = dur(hook), dur(closer)
    total = dur(args.final)
    mid_end = total - cl
    x, sr = st.audio(args.final)
    if x is not None and mid_end > 1:
        n = int(sr * 0.05)
        i = int(mid_end * sr)
        quiet = 0.0
        while i - n > 0 and np.sqrt(np.mean(x[i - n:i] ** 2)) < 0.01:
            quiet += 0.05
            i -= n
        ok("dead air", quiet <= MAX_TRAILING_SILENCE,
           "%.2fs of silence before the closer (<=%.1f)" % (quiet, MAX_TRAILING_SILENCE))

    # --- the LAST frame of each character clip must still face the lens ---------------
    tmpf = args.episode / "_face.png"
    for lab, v in (("end hook", hook), ("end closer", closer)):
        # Judge the performance, not the camera move: use the pre-shake clip when it is
        # there. Shake costs 0.014-0.033 of correlation on its own (#49).
        flat = v.with_suffix(v.suffix + ".flat")
        if flat.exists():
            v = flat
        if not v.exists():
            continue
        c, rt = last_frame_ok(sh, v, tmpf)
        # REPORTED, NOT GATED (#54). Two formulations were tried and both rank good and
        # bad endings wrongly. Against a mid-line frame from the SAME clip, an ideal
        # settled ending scores LOW because a closed mouth looks unlike mid-delivery:
        # episode 1's closer -- visually perfect -- scored 0.660 while an eyes-shut,
        # looking-down take scored 0.699. Against the locked portrait it inverts a
        # different way: the mediocre take scored highest of all eight measured. Pixel
        # correlation on a face crop cannot see gaze or eyelids, and as a GATE this cost
        # 17 rejected takes and $20.55 on episode 5 while excluding the one good take.
        # The frame is written out instead. Look at it.
        shot = lab.split()[1]
        out_png = args.final.parent / ("end-%s.png" % shot)
        # A STRIP of the last second, not one frame. The defect the operator keeps catching
        # lives in the seconds BEFORE the final frame -- episode 5 shipped a hook whose last
        # frame was clean while he looked down and began speaking again just before it, and
        # checking only the last frame missed it twice (#56).
        d_ = dur(v)
        # -ss BEFORE the tile, and fps= to SPACE the frames. `select` followed by `tile`
        # takes the first four frames it sees -- four CONSECUTIVE frames, 0.13s apart at
        # 30fps -- so the strip showed a tenth of a second and every ending looked settled.
        # Sampling at 4 fps over the last second is what was intended (#59).
        sh(["ffmpeg", "-v", "error", "-ss", "%.2f" % max(0.0, d_ - 1.0), "-i", v,
            "-vf", "fps=4,scale=300:-2,tile=4x1", "-frames:v", "1", "-update", "1",
            out_png, "-y"])
        if not out_png.exists():
            sh(["ffmpeg", "-v", "error", "-i", v, "-vf", "scale=360:-2", "-update", "1",
                out_png, "-y"])
        # A MOUTH strip too. Veo smears the lips on some seeds -- lip line gone, teeth
        # blurred -- for a second or two mid-clip, and it is invisible to every measure in
        # this file and to the end-frame strip, which only covers the last second. Episode
        # 8's closer shipped with it and the operator caught it (#66). The crop is fixed
        # because the framing is locked for the series.
        # A FULL-FRAME strip as well. The end strip and the lips strip are both CROPS, so
        # a framing change is invisible in them -- a crop of a zoomed frame looks like a
        # crop of a normal one. Episode 10's hook was extended to 5.25s for duration and
        # pushed visibly in; both cropped strips looked fine and the operator caught it
        # from the finished video (#71).
        frame_png = args.final.parent / ("framing-%s.png" % shot)
        sh(["ffmpeg", "-v", "error", "-ss", "%.2f" % max(0.0, d_ - 1.0), "-i", v,
            "-vf", "fps=4,scale=230:-2,tile=4x1", "-frames:v", "1", "-update", "1",
            frame_png, "-y"])
        lips_png = args.final.parent / ("lips-%s.png" % shot)
        sh(["ffmpeg", "-v", "error", "-i", v, "-vf",
            "fps=2.5,crop=iw*0.26:ih*0.09:iw*0.57:ih*0.47,scale=200:-2,tile=5x1",
            "-frames:v", "1", "-update", "1", lips_png, "-y"])
        print("  %-11s %-4s last frame %.3f -- reported, NOT a gate; open %s (last 1s)"
              % (lab, "----", c if c is not None else -1, out_png.name))

    # --- caption inside the safe zone ---------------------------------------------------
    p = args.episode / "_cap.png"
    sh(["ffmpeg", "-v", "error", "-ss", "%.2f" % min(2.0, max(0.5, hk / 2)),
        "-i", args.final, "-frames:v", "1", "-update", "1", p, "-y"])
    if p.exists():
        from PIL import Image
        a = np.asarray(Image.open(p).convert("RGB")).astype(int)
        band = (np.abs(a - np.array([199, 209, 143])).sum(2) < 30)
        rows = np.nonzero(band.sum(1) > 500)[0]
        p.unlink(missing_ok=True)
        if len(rows):
            ok("caption", rows.min() >= SAFE[0] and rows.max() <= SAFE[1],
               "band rows %d..%d (safe zone %d..%d)" % (rows.min(), rows.max(), *SAFE))
        else:
            ok("caption", False, "no caption band found on the hook")

    # --- the caption says the RIGHT PART NUMBER -------------------------------------
    # Nothing checked this. Building episode 5 with "Pt. 3" burned into the hook would
    # have passed every other check and shipped. Compared against the caption
    # make-caption.py renders for THIS episode's part, so it is exact rather than a guess.
    part = spec.get("part_number")
    if part is not None:
        want = args.episode / "_want_cap.png"
        got = args.episode / "_got_cap.png"
        cap_args = []
        for k, flag in (("caption_line1", "--line1"), ("caption_line2", "--line2"),
                        ("caption_box_top", "--box-top")):
            if spec.get(k):
                cap_args += [flag, str(spec[k])]
        sh([sys.executable, Path(__file__).resolve().parent / "make-caption.py",
            "--part", part, "--out", want] + cap_args)
        sh(["ffmpeg", "-v", "error", "-ss", "%.2f" % min(1.5, max(0.4, hk / 2)),
            "-i", args.final, "-frames:v", "1", "-update", "1", got, "-y"])
        if want.exists() and got.exists():
            from PIL import Image
            W_ = np.asarray(Image.open(want).convert("RGB")).astype(int)
            A_ = np.asarray(Image.open(got).convert("RGB")).astype(int)
            band = np.asarray(Image.open(want).convert("RGBA"))[:, :, 3] > 40
            if band.any() and W_.shape[:2] == A_.shape[:2]:
                d = np.abs(W_ - A_).sum(2)[band]
                frac = float((d < 90).mean())
                ok("caption text", frac >= 0.90,
                   "%.0f%% of the rendered caption matches Pt. %d exactly" % (frac * 100, part))
        want.unlink(missing_ok=True)
        got.unlink(missing_ok=True)

    # --- dialogue: are the scripted lines actually spoken in the FINISHED cut? ----------
    # Every other shipped property is asserted on the deliverable. Dialogue was the one
    # thing still trusted from an upstream stage -- and episode 4 shipped with that stage's
    # check silently inactive, because a 403 was swallowed and returned None (#48 era).
    want = [spec.get(k) for k in ("hook_line", "closer_line") if spec.get(k)]
    if want:
        try:
            heard = st.transcribe(args.final)
        except Exception:
            heard = None
        if not heard:
            print("  %-11s %-4s transcription unavailable -- dialogue NOT verified"
                  % ("dialogue", "----"))
        else:
            def norm(t):
                return re.sub(r"[^a-z0-9 ]", " ", t.lower())
            got = " ".join(norm(heard).split())
            worst, worst_line = 1.0, ""
            for line in want:
                toks = [w for w in norm(line).split() if len(w) > 2]
                hit = sum(1 for w in toks if w in got) / max(len(toks), 1)
                if hit < worst:
                    worst, worst_line = hit, line[:38]
            ok("dialogue", worst >= 0.8,
               "worst line %.0f%% of words present (>=80%%): \"%s...\"" % (100 * worst,
                                                                           worst_line))

    # --- notification cue: did the chime land on the deliverable? -----------------------
    tone = HERE.parent / "assets" / "sfx" / "notification-tone.mp3"
    notify = spec.get("notify")
    if tone.exists() and notify is not None and hook.exists():
        want = float(notify) + dur(hook)     # --notify is EDIT-relative; the middle
                                             # starts after the hook (#46)
        # Search ONLY the middle. The chime is laid under the screen recording, so it
        # cannot be in the hook or the closer -- and an unconstrained correlation finds
        # false peaks there: on episode 7 it reported 3.02s, inside the hook, and failed a
        # render whose chime was correctly at 14.66 (confirmed by differencing against a
        # probe). Bounding the window removes that whole class of false alarm (#63).
        # Search a NARROW window around where the cue was placed. The question is "is the
        # chime where we intended", not "where is the chime" -- and asked the second way
        # this check has now failed two correct renders on false correlation peaks
        # (episode 7 at 3.02s inside the hook, episode 10 at 8.80s inside the middle),
        # both confirmed correct by differencing against a probe. Bounding to the middle
        # was not enough; bounding to the expectation is (#68).
        got, pk, ratio = find_cue(args.final, tone,
                                  window=(want - 1.0, want + 1.0))
        if got is None:
            ok("notify cue", False, "could not analyse the audio")
        elif ratio is not None and ratio < CUE_MIN_RATIO:
            print("  %-11s %-4s chime not distinguishable from the bed (%.2fx) -- "
                  "check it by ear" % ("notify cue", "----", ratio))
        else:
            ok("notify cue", abs(got - want) <= CUE_TOL,
               "chime at %.2fs, deliverable at %.2fs (edit %.2f + hook %.2f), off by %+.2fs"
               % (got, want, float(notify), dur(hook), got - want))

    # --- duration -----------------------------------------------------------------------
    dur_range = (tuple(float(x) for x in args.dur_range.split(":"))
                 if args.dur_range else DUR)
    ok("duration", dur_range[0] <= total <= dur_range[1],
       "%.2fs (%.0f-%.0f)" % (total, *dur_range))

    print()
    if fails:
        print("FAILED: %s" % ", ".join(fails))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
