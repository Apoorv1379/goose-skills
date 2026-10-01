#!/usr/bin/env python3
"""Remove the music bed H3 bakes under the dialogue. Mandatory on every H3 clip.

WHY THIS EXISTS
H3 generates a music bed with the picture and prompting cannot remove it. That was
established the expensive way: six takes across two prompt versions and three camera
levels all measured a -28.0 to -28.4 dB noise floor, against Veo's -36.2 dB. The second
prompt used H3's own recommended structure -- a trailing Sound: clause naming exactly
what should be heard, then every musical path closed explicitly -- and moved the floor
0.3 dB. It is learned behaviour, not an instruction-following failure. Do not spend
generations trying to prompt it away.

WHY NOT clean-character-audio.sh
That chain works on Veo's ambience because the bed is a steady -31..-35 dB plateau and
speech sits at -20..-27 dB, so a fixed-threshold gate separates them by level alone.
This bed is music: it is not steady, and it overlaps the voice. After normalisation it
sits ~6 dB under the speech -- far too close for a gate that does not chew consonants.

WHAT THIS DOES INSTEAD
Source separation (Demucs htdemucs, two-stem vocals/no-vocals), keeping the vocal stem.
Verified on a shipped take:

    noise floor   -28.3 -> -34.9 dB   (6.6 dB of music removed)
    voice band    -21.9 -> -22.0 dB   (unchanged)
    HF clarity    -23.9 -> -23.9 dB   (unchanged)
    sibilance     -31.1 -> -30.7 dB   (unchanged)
    median F0      160.0 -> 158.4 Hz  (1.6 Hz -- same speaker)

The separated bed measured spectral flatness 0.191 with 40.4 dB tonal peaks and 38 % of
its energy below 120 Hz. That is a bass-led musical bed, not room tone -- which is why a
denoiser was never going to touch it.

ORDER OF OPERATIONS
Run this BEFORE clean-character-audio.sh. This removes the music; that one levels what
is left. Running them the other way round levels the music along with the voice.

Video is stream-copied; only the audio track is rewritten.

Usage:
    strip-music.py <clip.mp4> [more.mp4 ...] [--out-dir DIR] [--keep-bed] [--jobs N]

    --keep-bed  also writes <name>-bed.wav, the isolated music. Useful as evidence when
                someone asks whether the bed was really there.

First run downloads the htdemucs weights (~80 MB) from Hugging Face. Roughly 25 s per
6 s clip on CPU.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def measure(path: Path, filt: str = "highpass=f=300,lowpass=f=3400") -> str:
    import re
    null = "NUL" if sys.platform == "win32" else "/dev/null"
    out = run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
               filt + ",volumedetect", "-f", "null", null])
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out.stdout + out.stderr)
    return m.group(1) if m else "?"


def gate_gaps(wav_in: Path, wav_out: Path, duck_db: float = -26.0) -> tuple:
    """Duck the non-speech gaps, where separation leaves audible residue.

    Separation is very good inside speech and imperfect in the gaps: a percussive hit has
    no vocal content to key on, so a beat can survive into the vocal stem. On the first
    clip cleaned this way a leftover beat sat at 5.70-5.95s, in the tail after the line
    ends, measuring a bass/voice ratio of 2.01 against ~0.00 during speech.

    Those gaps carry no speech to protect -- measured voice-band energy there was three
    orders of magnitude below the spoken passages -- so they can be ducked hard. Ducked,
    not silenced: dropping to absolute zero sounds like the recording died, so a trace of
    room is left in. Ramps are 80 ms to avoid clicking.

    Returns (gaps_ducked, seconds_ducked).
    """
    import wave
    import numpy as np

    with wave.open(str(wav_in)) as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768.0
    mono = x.reshape(-1, ch).mean(axis=1) if ch > 1 else x

    hop = max(1, int(sr * 0.02))
    frames = max(1, len(mono) // hop)
    # Voice-band envelope drives the decision, so a bass-only residue never reads as speech.
    band = np.fft.rfftfreq(hop * 2, 1 / sr)
    keep = (band > 300) & (band < 3400)
    env = np.zeros(frames)
    for i in range(frames):
        seg = mono[i * hop:i * hop + hop * 2]
        if len(seg) < hop * 2:
            seg = np.pad(seg, (0, hop * 2 - len(seg)))
        env[i] = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))[keep].sum()
    if env.max() <= 0:
        return 0, 0.0
    speech = env > env.max() * 0.02          # 34 dB below the loudest voice frame
    pad = int(0.10 / 0.02)                   # keep 100 ms either side of every phrase
    padded = speech.copy()
    for i in np.flatnonzero(speech):
        padded[max(0, i - pad):i + pad + 1] = True

    gain = np.where(padded, 1.0, 10 ** (duck_db / 20.0))
    g = np.repeat(gain, hop)[:len(mono)]
    if len(g) < len(mono):
        g = np.pad(g, (0, len(mono) - len(g)), constant_values=g[-1] if len(g) else 1.0)
    # 80 ms smoothing ramp
    k = max(1, int(sr * 0.08))
    g = np.convolve(g, np.ones(k) / k, mode="same")

    y = (x.reshape(-1, ch) * g[:, None]).reshape(-1) if ch > 1 else x * g
    with wave.open(str(wav_out), "wb") as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())

    ducked = int((~padded).sum())
    # count contiguous ducked runs
    runs = np.diff(np.concatenate(([0], (~padded).astype(int), [0])))
    return int((runs == 1).sum()), round(ducked * 0.02, 2)


def strip(clip: Path, out_dir: Path, keep_bed: bool, no_gate: bool = False) -> Path | None:
    tmp = Path(tempfile.mkdtemp(prefix="stripmus_"))
    try:
        wav = tmp / "in.wav"
        r = run(["ffmpeg", "-v", "error", "-i", str(clip), "-vn", "-ac", "2",
                 "-ar", "44100", str(wav), "-y"])
        if not wav.exists():
            print("  ERROR: could not extract audio: %s" % r.stderr.strip()[:200],
                  file=sys.stderr)
            return None

        before = measure(clip)
        r = run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
                 "-o", str(tmp / "sep"), str(wav)])
        stem = tmp / "sep" / "htdemucs" / "in"
        vocals = stem / "vocals.wav"
        if not vocals.exists():
            print("  ERROR: demucs produced no vocal stem.\n%s"
                  % (r.stderr or r.stdout).strip()[-400:], file=sys.stderr)
            return None

        gated = ""
        if not no_gate:
            g = stem / "vocals-gated.wav"
            try:
                runs, secs = gate_gaps(vocals, g)
                if g.exists():
                    vocals = g
                    gated = "   gaps ducked: %d (%.2fs)" % (runs, secs)
            except Exception as e:  # noqa: BLE001 - never lose the clip over the gate
                print("  WARNING: gap gate skipped (%s)" % e, file=sys.stderr)

        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / clip.name
        if out.resolve() == clip.resolve():
            out = out_dir / (clip.stem + "-nomusic.mp4")
        run(["ffmpeg", "-v", "error", "-i", str(clip), "-i", str(vocals),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
             "-b:a", "192k", "-ar", "48000", "-shortest",
             "-movflags", "+faststart", str(out), "-y"])
        if not out.exists():
            print("  ERROR: failed to mux the cleaned audio back", file=sys.stderr)
            return None

        if keep_bed and (stem / "no_vocals.wav").exists():
            shutil.copy2(stem / "no_vocals.wav", out_dir / (clip.stem + "-bed.wav"))

        after = measure(out)
        # The voice band must not move. If it does, the separation took speech with it.
        try:
            delta = abs(float(after) - float(before))
            warn = "   <-- WARNING: voice band moved %.1f dB, inspect this take" % delta \
                if delta > 1.0 else ""
        except ValueError:
            warn = ""
        print("  %s -> %s   voice band %s -> %s dB%s%s"
              % (clip.name, out.name, before, after, warn, gated))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--out-dir", type=Path,
                    help="where to write cleaned clips (default: alongside the input, "
                         "suffixed -nomusic)")
    ap.add_argument("--keep-bed", action="store_true",
                    help="also write the isolated music stem as <name>-bed.wav")
    ap.add_argument("--no-gate", action="store_true",
                    help="skip the gap-ducking pass. Separation leaves residue in "
                         "non-speech gaps (a percussive hit has no vocal content to key "
                         "on); the gate removes it. Only skip this if the gaps must stay "
                         "untouched")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg must be on PATH.")
    try:
        import demucs  # noqa: F401
    except ImportError:
        sys.exit("ERROR: demucs not installed. `pip install demucs torchaudio`.")

    missing = [c for c in args.clips if not c.exists()]
    if missing:
        sys.exit("ERROR: no such file: " + ", ".join(str(m) for m in missing))

    ok = 0
    for c in args.clips:
        print("stripping %s ..." % c.name)
        if strip(c, args.out_dir or c.parent, args.keep_bed, args.no_gate):
            ok += 1
    print("\n%d/%d cleaned. Run clean-character-audio.sh on these next, not before."
          % (ok, len(args.clips)))
    return 0 if ok == len(args.clips) else 1


if __name__ == "__main__":
    raise SystemExit(main())
