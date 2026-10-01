#!/usr/bin/env python3
"""Cut the results stills to the voiceover's own word timings.

The series rule is that nothing appears before or after the word that names it. Cutting
the results section on round numbers breaks that by a beat or two every time, and it
reads as a slideshow running next to the voice instead of with it.

So: transcribe the VO line with word timestamps (the same scribe-v2 call `trim-tail.py`
uses), find the word each still is pinned to, and cut there. The last still holds to the
end of the line plus --hold.

  pin-broll.py --vo final/vo-c.mp3 \\
      --pin "six:ads-1.jpg" --pin "gallery:ads-2.jpg" \\
      --pin "voice:ads-3.jpg" --pin "feed:ads-4.jpg" \\
      --out working/results.mp4

A pin is WORD:PATH. WORD is matched case-insensitively against the transcript, first
occurrence at or after the previous pin, so repeated words behave. The first still starts
at 0.0 regardless of its word, because the section has to be covered from its first frame.

Environment: FAL_KEY (or FAL_API_KEY).
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit((r.stderr or r.stdout)[-1500:])
    return r


def dur(p: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", p]).stdout.strip())


def words_of(vo: Path) -> list[dict]:
    if "FAL_KEY" not in os.environ and "FAL_API_KEY" in os.environ:
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
    if not os.environ.get("FAL_KEY"):
        sys.exit("ERROR: FAL_KEY (or FAL_API_KEY) not set.")
    from fal_client import subscribe, upload_file
    r = subscribe("fal-ai/elevenlabs/speech-to-text/scribe-v2",
                  arguments={"audio_url": upload_file(str(vo)), "language_code": "eng"})
    ws = [w for w in ((r or {}).get("words") or []) if (w.get("text") or "").strip()]
    if not ws:
        sys.exit("ERROR: the transcript came back with no word timings.")
    return ws


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vo", required=True, type=Path)
    ap.add_argument("--pin", action="append", required=True, metavar="WORD:PATH")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--hold", type=float, default=0.35,
                    help="seconds the last still holds after the line ends")
    a = ap.parse_args()

    pins = []
    for p in a.pin:
        word, _, path = p.partition(":")
        still = Path(path)
        if not still.exists():
            sys.exit("no such still: %s" % still)
        pins.append((word.strip().lower(), still))

    ws = words_of(a.vo)
    norm = [(re.sub(r"[^a-z0-9']", "", (w.get("text") or "").lower()),
             float(w.get("start", 0.0))) for w in ws]
    line_end = max(float(w.get("end", w.get("start", 0.0))) for w in ws)

    starts, cursor = [], 0
    for word, _ in pins:
        hit = next((i for i in range(cursor, len(norm)) if norm[i][0] == word), None)
        if hit is None:
            sys.exit("ERROR: the word %r is not in the voiceover. Heard: %s"
                     % (word, " ".join(t for t, _ in norm)))
        starts.append(norm[hit][1])
        cursor = hit + 1

    starts[0] = 0.0                       # the section must be covered from frame one
    ends = starts[1:] + [line_end + a.hold]
    shots = []
    for (word, still), s, e in zip(pins, starts, ends):
        if e - s < 0.2:
            sys.exit("ERROR: '%s' leaves only %.2fs on screen. Pin it to a word further "
                     "apart in the line." % (word, e - s))
        shots.append("%s:%.3f" % (still, e - s))
        print("  %-10s %5.2f -> %5.2f  (%.2fs)  %s" % (word, s, e, e - s, still.name))

    sh([sys.executable, HERE / "make-broll.py", "--out", a.out,
        *sum((["--shot", s] for s in shots), [])])
    print("[pin-broll] %s  %.2fs against a %.2fs line (+%.2fs hold)"
          % (a.out, dur(a.out), line_end, a.hold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
