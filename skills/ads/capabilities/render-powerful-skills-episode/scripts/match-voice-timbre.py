#!/usr/bin/env python3
"""Match every audio asset in an episode to the HOOK's voice, by spectrum not just pitch.

WHY PITCH IS NOT ENOUGH
Episode 3 shipped its four assets within 2.3 Hz of each other -- the tightest F0 spread of
the series -- and still sounded inconsistent. F0 says two voices are at the same pitch; it
says nothing about whether they sound like the same person in the same room. The
voiceover came from the TTS clone and the character clips from Veo, and the two engines
have different spectral signatures: measured, the clone was ~6 dB heavy below 200 Hz and
4-8 dB short of presence between 1.3 and 5 kHz, which is the "different microphone" sound.

THE BAR COMES FROM A SHIPPED EPISODE, NOT FROM TASTE
Long-term-average-spectrum correlation against the episode's own hook:

    episode 2 (shipped, judged to sound right)   hook/closer 0.946, hook/VO 0.965 and 0.955
    episode 3 as first built                     hook/closer 0.926, hook/VO 0.875 and 0.850

So 0.946 is the floor, taken from the worst pair inside an episode that was accepted.

WHAT THIS DOES
Derives a smoothed, clamped correction curve from the reference's long-term average
spectrum to each target's, and applies it with `firequalizer`. The hook is the reference
because it is the take the voice was judged on -- everything else is matched to it.

Pitch is left alone: correct F0 first (make-vo.py does), then run this. Re-check F0 after,
since a large low-frequency correction can move the measured median a little.

Usage:
    match-voice-timbre.py --ref <episode>/final/hook.mp4 \\
        --target <episode>/final/vo-a.mp3 --target <episode>/final/closer.mp4
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

N = 1024
VOICED_RMS = 0.02
SMOOTH = 5            # bins; less smoothing tracks the difference more closely
CLAMP = 12.0          # dB, either way
POINTS = (60, 100, 150, 200, 260, 330, 420, 530, 670, 850, 1100, 1400,
          1800, 2300, 2900, 3700, 4700, 6000, 7500)


def sh_(cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True)


def sample_rate(p: Path) -> int:
    """The file's OWN rate -- asetrate with a guessed base is a real failure mode."""
    o = sh_(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate", "-of", "csv=p=0", p]).stdout.strip()
    return int(o) if o.isdigit() else 44100


def _st():
    import importlib.util as u
    spec = u.spec_from_file_location("st", Path(__file__).resolve().parent / "screen-takes.py")
    m = u.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ltas(m, p: Path):
    """Average magnitude spectrum over VOICED frames only. Silence would flatten it."""
    x, sr = m.audio(p)
    if x is None:
        return None, None
    acc = np.zeros(N // 2 + 1)
    cnt = 0
    for i in range(0, len(x) - N, N // 2):
        s = x[i:i + N]
        if np.sqrt(np.mean(s ** 2)) < VOICED_RMS:
            continue
        acc += np.abs(np.fft.rfft(s * np.hanning(N)))
        cnt += 1
    if cnt < 5:
        return None, None
    a = acc / cnt
    return a / (a.sum() + 1e-12), np.fft.rfftfreq(N, 1 / sr)


def sim(a, b) -> float:
    return float(np.corrcoef(np.log(a + 1e-9), np.log(b + 1e-9))[0, 1])


def noise_floor(m, p: Path) -> float | None:
    """Level of the quietest fifth -- the room tone between words."""
    x, sr = m.audio(p)
    if x is None:
        return None
    n = int(sr * 0.05)
    q = sorted(float(np.sqrt(np.mean(x[i:i + n] ** 2)))
               for i in range(0, len(x) - n, n))
    quiet = q[:max(3, len(q) // 5)]
    return round(float(20 * np.log10(np.mean(quiet) + 1e-12)), 1)


def add_room_tone(m, ref: Path, target: Path, out: Path) -> tuple:
    """Lay the reference's own room tone under a dry target.

    This is the difference the spectrum match does NOT fix. A Veo clip is a voice recorded
    in a room and carries its air; the TTS clone is studio-dry. Measured on episode 3, the
    hook's floor sat at -41.5 dB and the clone's at -51.0 -- so at every gap between words
    the episode dropped into a silence the character clips never have, which reads as the
    voice coming from somewhere else. Episode 2, which was accepted, had its VO within
    2.4 dB of its hook because both came off the same engine.

    The tone is taken from the reference's own quiet frames, so it is that room, not a
    generic hiss. Mixed in numpy rather than with `aloop`, which hung on a large size.
    """
    rx, rsr = m.audio(ref)
    tx, tsr = m.audio(target)
    if rx is None or tx is None:
        return None, None
    n = int(rsr * 0.05)
    frames = [(float(np.sqrt(np.mean(rx[i:i + n] ** 2))), i)
              for i in range(0, len(rx) - n, n)]
    frames.sort()
    quiet = [rx[i:i + n] for _, i in frames[:max(6, len(frames) // 5)]]
    if not quiet:
        return None, None
    tone = np.concatenate(quiet)
    reps = int(np.ceil(len(tx) / len(tone))) + 1
    bed = np.tile(tone, reps)[:len(tx)]
    # Crossfade the tile joins so the loop does not tick.
    j = len(tone)
    f = int(rsr * 0.01)
    for k in range(j, len(bed) - f, j):
        ramp = np.linspace(0, 1, f)
        bed[k:k + f] = bed[k:k + f] * ramp + bed[k - f:k] * (1 - ramp)

    want = 10 ** (noise_floor(m, ref) / 20)
    have = np.sqrt(np.mean(bed ** 2)) + 1e-12
    y = np.clip(tx + bed * (want / have), -1.0, 1.0)

    import wave
    tmpw = out.with_suffix(".wav")
    with wave.open(str(tmpw), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tsr)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmpw), "-c:a", "libmp3lame",
                    "-q:a", "2", str(out), "-y"], capture_output=True)
    tmpw.unlink(missing_ok=True)
    return noise_floor(m, ref), noise_floor(m, out)


def centroid(m, p: Path) -> float | None:
    """Median spectral centroid over voiced frames -- how BRIGHT the voice is.

    LTAS correlation is TILT-BLIND: it compares spectral shape after log scaling, so a
    clip can correlate at 0.965 with the reference and still be half again as bright.
    Episode 7's closer measured 1552 Hz against a 1101 Hz hook and a 1034 Hz reference,
    passed every existing check, and the operator heard it immediately as a different
    voice (#64).
    """
    x, sr = m.audio(p)
    if x is None:
        return None
    N = 1024
    out = []
    for i in range(0, len(x) - N, N // 2):
        s_ = x[i:i + N]
        if np.sqrt(np.mean(s_ ** 2)) < 0.02:
            continue
        mag = np.abs(np.fft.rfft(s_ * np.hanning(N)))
        fr = np.fft.rfftfreq(N, 1.0 / sr)
        out.append(float((mag * fr).sum() / (mag.sum() + 1e-9)))
    return float(np.median(out)) if out else None


def match_brightness(m, src: Path, target_hz: float, tol: float = 0.06):
    """Sweep a treble shelf until the centroid lands on the reference.

    The correction curve in curve() is smoothed and clamped, so it cannot apply a large
    tilt -- on episode 7's closer it moved the centroid 1552 -> 1510 when 1101 was needed.
    A shelf can. Measured on that clip: -3 dB gave 1303, -5 gave 1149, -7 gave 1054, so the
    response is roughly 75 Hz per dB and a sweep converges in a few steps.
    """
    cur = centroid(m, src)
    if cur is None or target_hz is None:
        return None, None
    if abs(cur - target_hz) / target_hz <= tol:
        return cur, 0.0
    best = (abs(cur - target_hz), 0.0, cur)
    tmp = src.with_name(src.stem + "-br" + src.suffix)
    for g in [-1.0 * k for k in range(1, 15)]:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-af",
                            "treble=g=%.1f:f=2000:width_type=q:w=0.7" % g,
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                            str(tmp), "-y"], capture_output=True)
        if not tmp.exists():
            continue
        v = centroid(m, tmp)
        if v is None:
            continue
        if abs(v - target_hz) < best[0]:
            best = (abs(v - target_hz), g, v)
        if v <= target_hz:
            break
    _, g, v = best
    if g != 0.0:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-af",
                        "treble=g=%.1f:f=2000:width_type=q:w=0.7" % g,
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                        str(tmp), "-y"], capture_output=True)
        if tmp.exists() and tmp.stat().st_size > 10000:
            src.write_bytes(tmp.read_bytes())
    tmp.unlink(missing_ok=True)
    return v, g


def curve(ref, tgt, fr) -> str:
    d = 20 * np.log10((ref + 1e-9) / (tgt + 1e-9))
    d = np.convolve(d, np.ones(SMOOTH) / SMOOTH, mode="same")
    d = np.clip(d, -CLAMP, CLAMP)
    return ";".join("entry(%d,%.1f)" % (f, d[int(np.argmin(np.abs(fr - f)))]) for f in POINTS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, type=Path, help="usually the episode's hook")
    ap.add_argument("--target", action="append", required=True, type=Path)
    ap.add_argument("--floor", type=float, default=0.946,
                    help="similarity floor, from shipped episode 2's worst internal pair")
    ap.add_argument("--room-tone", action="store_true",
                    help="also lay the reference's own room tone under audio-only "
                         "targets, so the gaps between words are not dead silent")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg not on PATH.")
    m = _st()
    R, fr = ltas(m, args.ref)
    if R is None:
        sys.exit("ERROR: could not analyse the reference.")
    rx, rsr = m.audio(args.ref)
    ref_f0 = m.f0_median(rx, rsr)
    ref_centroid = centroid(m, args.ref)
    print("reference: %s   F0 %.1f Hz   centroid %.0f Hz\n"
          % (args.ref.name, ref_f0, ref_centroid or -1))
    print("%-26s %-9s %-9s %-9s %s" % ("target", "before", "after", "F0", "ok"))

    bad = []
    for t in args.target:
        if not t.exists():
            print("  missing %s" % t)
            bad.append(t.name)
            continue
        T0, _ = ltas(m, t)
        if T0 is None:
            print("  %-24s could not analyse" % t.name)
            bad.append(t.name)
            continue
        before = sim(R, T0)
        nf_before = noise_floor(m, t)
        if args.dry_run:
            print("%-26s %-9.3f (dry run)" % (t.name, before))
            continue

        if t.suffix.lower() in (".mp4", ".mov", ".mkv"):
            gain = curve(R, T0, fr)
            tmp = t.with_name(t.stem + "-tm" + t.suffix)
            sh_(["ffmpeg", "-v", "error", "-i", t, "-af", "firequalizer=gain_entry='%s'" % gain,
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", tmp, "-y"])
            after = before
            if tmp.exists() and tmp.stat().st_size > 5000:
                cand = sim(R, ltas(m, tmp)[0])
                if cand > before:
                    t.write_bytes(tmp.read_bytes())
                    after = cand
            tmp.unlink(missing_ok=True)
            x, sr = m.audio(t)
            f0 = m.f0_median(x, sr)
            ok = after >= args.floor
            if not ok:
                bad.append(t.name)
            # Brightness LAST. curve() is smoothed and clamped, so it corrects spectral
            # SHAPE but not a large tilt -- and `sim` is tilt-blind, so a clip can pass at
            # 0.965 while being half again as bright. Episode 7's closer measured 1552 Hz
            # against a 1101 Hz hook, passed every check, and was heard immediately as a
            # different voice. The EQ moved it to 1510; a shelf reaches 1088 (#64).
            cb = centroid(m, t)
            ca, g = match_brightness(m, t, ref_centroid)
            print("%-26s %-9.3f %-9.3f %-9.1f %-5s %s"
                  % (t.name, before, after, f0, "yes" if ok else "BELOW FLOOR",
                     ("bright %.0f->%.0f Hz (%+.1f dB)" % (cb, ca, g))
                     if (cb and ca and g) else "bright ok"))
            continue

        # AUDIO: ONE pass. Pitch is chosen to compensate for what the EQ does to F0.
        # The equaliser's low cut raises the measured fundamental by roughly 4 Hz whatever
        # it started at (measured: no pre-shift -> +8.5, 0.94 -> +4.1, 0.97 -> +3.5). So
        # correcting pitch AFTER the EQ only fights it, and on a third lossy encode it
        # collapsed similarity from 0.965 to 0.698. Search the PRE-shift and encode once.
        best = None
        tmp = t.with_name(t.stem + "-tm.mp3")
        tmp2 = t.with_name(t.stem + "-tm2.mp3")
        srate = sample_rate(t)
        for f in [0.90 + 0.01 * i for i in range(21)]:
            sh_(["ffmpeg", "-v", "error", "-i", t, "-af",
                 "asetrate=%d*%.4f,aresample=%d,atempo=%.4f" % (srate, f, srate, 1 / f),
                 "-c:a", "libmp3lame", "-q:a", "2", tmp, "-y"])
            if not tmp.exists():
                continue
            Ts, _ = ltas(m, tmp)
            if Ts is None:
                continue
            # ffmpeg cannot read and write the same path -- that silently produced a
            # corrupt file and made every candidate fail the floor.
            sh_(["ffmpeg", "-v", "error", "-i", tmp, "-af",
                 "firequalizer=gain_entry='%s'" % curve(R, Ts, fr),
                 "-c:a", "libmp3lame", "-q:a", "2", tmp2, "-y"])
            if not tmp2.exists():
                continue
            xx, ss = m.audio(tmp2)
            v = m.f0_median(xx, ss)
            Tf, _ = ltas(m, tmp2)
            if v is None or Tf is None:
                continue
            s2 = sim(R, Tf)
            if s2 < args.floor:
                continue
            if best is None or abs(v - ref_f0) < best[0]:
                best = (abs(v - ref_f0), f, v, s2, tmp2.read_bytes())
        tmp.unlink(missing_ok=True)
        tmp2.unlink(missing_ok=True)
        if best is None:
            print("%-26s %-9.3f  no setting cleared the floor" % (t.name, before))
            bad.append(t.name)
            continue
        _, f, v, after, blob = best
        t.write_bytes(blob)

        if args.room_tone:
            rt = t.with_name(t.stem + "-rt.mp3")
            rf, tf = add_room_tone(m, args.ref, t, rt)
            if rt.exists() and rt.stat().st_size > 5000:
                t.write_bytes(rt.read_bytes())
                after = sim(R, ltas(m, t)[0])
                x, sr = m.audio(t)
                v = m.f0_median(x, sr)
                print("%-26s room tone: floor %s -> %s dB (reference %s)"
                      % ("", nf_before, tf, rf))
            rt.unlink(missing_ok=True)

        ok = after >= args.floor
        if not ok:
            bad.append(t.name)
        print("%-26s %-9.3f %-9.3f %-9.1f %s [shift %.2f]"
              % (t.name, before, after, v, "yes" if ok else "BELOW FLOOR", f))

    print("\nfloor %.3f (shipped episode 2's worst internal pair)" % args.floor)
    if bad:
        print("still short: %s" % ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
