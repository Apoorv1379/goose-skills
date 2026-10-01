#!/usr/bin/env python3
"""Show each take's gesture against the WORD it is supposed to land on.

A gesture that arrives half a second after the word reads as a dub. The prompt asks for
the timing (#87) but the model obeys it unevenly, so this is the screening pass: it finds
the word in each take's own audio and lays out the frames around it, one row per take,
with the frame ON the word marked.

  check-gesture.py hook-meta/*.mp4 --word one --out gesture.png

The sheet is for the eye -- there is no automatic "is the finger up" test, and a bad one
would be worse than none. What this removes is the guesswork about WHERE to look.

Environment: FAL_KEY (or FAL_API_KEY).
"""
import argparse, os, re, subprocess, sys, tempfile
from pathlib import Path

OFFSETS = (-0.30, 0.0, 0.30, 0.60)      # frames either side of the word's start


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit((r.stderr or r.stdout)[-1200:])
    return r


def dur(p: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", p]).stdout.strip())


def word_time(clip: Path, word: str) -> float | None:
    from fal_client import subscribe, upload_file
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        sh(["ffmpeg", "-v", "error", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", wav, "-y"])
        r = subscribe("fal-ai/elevenlabs/speech-to-text/scribe-v2",
                      arguments={"audio_url": upload_file(str(wav)), "language_code": "eng"})
    for w in ((r or {}).get("words") or []):
        if re.sub(r"[^a-z0-9']", "", (w.get("text") or "").lower()) == word.lower():
            return float(w.get("start", 0.0))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--word", required=True)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()

    if "FAL_KEY" not in os.environ and "FAL_API_KEY" in os.environ:
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
    if not os.environ.get("FAL_KEY"):
        sys.exit("ERROR: FAL_KEY (or FAL_API_KEY) not set.")

    with tempfile.TemporaryDirectory() as td:
        work, rows = Path(td), []
        for clip in a.clips:
            t = word_time(clip, a.word)
            if t is None:
                print("  %-22s never says %r -- skipped" % (clip.name, a.word))
                continue
            print("  %-22s says %r at %.2fs" % (clip.name, a.word, t))
            shots = []
            # a trimmed take can end inside the window, and ffmpeg then writes no frame
            # at all, which took out the whole sheet. Clamp to the last frame instead.
            end = dur(clip) - 0.05
            for k, off in enumerate(OFFSETS):
                f = work / ("%s_%d.png" % (clip.stem, k))
                sh(["ffmpeg", "-v", "error", "-ss", "%.3f" % min(max(0.0, t + off), end),
                    "-i", clip,
                    "-frames:v", "1", f, "-y"])
                # mark the frame that sits ON the word
                mark = ",drawbox=x=0:y=0:w=iw:h=14:color=red@0.9:t=fill" if off == 0.0 else ""
                g = work / ("%s_%d_s.png" % (clip.stem, k))
                sh(["ffmpeg", "-v", "error", "-i", f, "-vf", "scale=300:-1" + mark, g, "-y"])
                shots.append(g)
            row = work / ("row_%s.png" % clip.stem)
            sh(["ffmpeg", "-v", "error", *sum((["-i", str(s)] for s in shots), []),
                "-filter_complex", "".join("[%d]" % i for i in range(len(shots)))
                + "hstack=%d" % len(shots), row, "-y"])
            rows.append(row)

        if not rows:
            sys.exit("ERROR: no take said %r." % a.word)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        sh(["ffmpeg", "-v", "error", *sum((["-i", str(r)] for r in rows), []),
            "-filter_complex", "".join("[%d]" % i for i in range(len(rows)))
            + ("vstack=%d" % len(rows) if len(rows) > 1 else "null"), a.out, "-y"])
    print("[gesture] %s  -- columns are %s seconds around the word, red bar is ON it"
          % (a.out, ", ".join("%+.2f" % o for o in OFFSETS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
