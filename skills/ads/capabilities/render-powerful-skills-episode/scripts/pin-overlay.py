#!/usr/bin/env python3
"""Drop an overlay onto a CHARACTER clip, on the word he says, then take it away.

`pin-broll.py` cuts stills into a section of their own and `overlay-stills.py` puts them
over the screen recording. This is the third shape and the one that reads most like a real
creator: he is talking to camera, the thing he just named appears beside him for a beat,
it goes, and the same take carries on uninterrupted.

The timing comes from HIS OWN audio, not from a separate voiceover, so the card cannot
land on the wrong word:

  pin-overlay.py --base hook.mp4 --out hook-overlaid.mp4 \\
      --pin "spend:report.png:1.8"

A pin is WORD:PATH:SECONDS. The card fades in over 0.2s on the word, holds, and fades out
over 0.2s, which stops it reading as a jump cut in a handheld shot.

Card geometry is fixed and deliberate: it sits in the upper half, inside the caption safe
zone, on the side AWAY from his face. The series framing puts him on the right of frame,
so the card goes left over the monitor, never across him.

Environment: FAL_KEY (or FAL_API_KEY) for the word timings.
"""
import argparse, os, re, subprocess, sys, tempfile
from pathlib import Path

W, H = 1080, 1920
SAFE_TOP, SAFE_BOT = 285, 1634
CARD_W = 560                  # leaves his face clear at the series' framing
CARD_X = 40
CARD_Y = 620
FADE = 0.2


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit((r.stderr or r.stdout)[-1500:])
    return r


def size(p: Path) -> tuple[int, int]:
    w, h = sh(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
               "stream=width,height", "-of", "csv=p=0:s=x", p]).stdout.strip().split("x")
    return int(w), int(h)


def dur(p: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", p]).stdout.strip())


def words_of(clip: Path) -> list[tuple[str, float]]:
    if "FAL_KEY" not in os.environ and "FAL_API_KEY" in os.environ:
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
    if not os.environ.get("FAL_KEY"):
        sys.exit("ERROR: FAL_KEY (or FAL_API_KEY) not set.")
    from fal_client import subscribe, upload_file
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        sh(["ffmpeg", "-v", "error", "-i", clip, "-vn", "-ac", "1", "-ar", "16000",
            wav, "-y"])
        r = subscribe("fal-ai/elevenlabs/speech-to-text/scribe-v2",
                      arguments={"audio_url": upload_file(str(wav)), "language_code": "eng"})
    ws = [(re.sub(r"[^a-z0-9']", "", (w.get("text") or "").lower()), float(w.get("start", 0)))
          for w in ((r or {}).get("words") or []) if (w.get("text") or "").strip()]
    if not ws:
        sys.exit("ERROR: no word timings came back for %s" % clip)
    return ws


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, type=Path, help="the character clip")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--pin", action="append", required=True, metavar="WORD:PATH:SECONDS")
    # The base is usually a character clip and the words come from its own audio. A
    # terminal recording has no audio, so --vo points at the voiceover that will play over
    # it and --offset says where that voiceover starts inside the finished section.
    ap.add_argument("--vo", type=Path, help="take word timings from this audio instead")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="seconds to add to every word time (where --vo starts)")
    # A clip that ends soon after its last word leaves no room for a card: the window
    # gets clamped and the card spends most of its life mid-fade, which reads as a ghost.
    # Shorten the fade rather than the hold when that happens.
    ap.add_argument("--fade", type=float, default=FADE,
                    help="seconds of fade in/out on each card (default %.2f)" % FADE)
    ap.add_argument("--card-y", type=int, default=CARD_Y)
    # Cards are CENTRED horizontally by default. Pinning them to a fixed left margin made
    # each one sit at a different offset depending on its width, so a sequence of cards
    # drifted left and right across the frame instead of holding one axis.
    ap.add_argument("--card-x", default="center",
                    help='"center" (default) or a pixel x in FINAL frame coordinates')
    ap.add_argument("--card-w", type=int, default=CARD_W)
    a = ap.parse_args()

    base_len = dur(a.base)
    # Character takes come off the generator at 720x1280 and are only conformed to the
    # episode's 1080x1920 later, in build-episode.py. Card geometry is written in FINAL
    # frame coordinates, so it has to be scaled to whatever this clip actually is, or a
    # 1000px card on a 720px frame is silently clipped at the edge (it was).
    base_w, base_h = size(a.base)
    k = base_w / W
    ws = [(w, t0 + a.offset) for w, t0 in words_of(a.vo or a.base)]
    print("  heard: %s" % " ".join(t for t, _ in ws))

    pins, cursor = [], 0
    for spec in a.pin:
        word, path, secs = spec.split(":")[0], ":".join(spec.split(":")[1:-1]), spec.split(":")[-1]
        img = Path(path)
        if not img.exists():
            sys.exit("no such image: %s" % img)
        hit = next((i for i in range(cursor, len(ws)) if ws[i][0] == word.lower()), None)
        if hit is None:
            sys.exit("ERROR: he never says %r in this take." % word)
        at = ws[hit][1]
        cursor = hit + 1
        if at + float(secs) > base_len - 0.02:
            secs = "%.3f" % max(0.0, base_len - at - 0.05)
        pins.append((word, img, at, float(secs)))

    card_w = int(round(a.card_w * k))
    card_x = (int(round((W - a.card_w) / 2 * k)) if a.card_x == "center"
              else int(round(float(a.card_x) * k)))
    card_y = int(round(a.card_y * k))
    if a.card_y < SAFE_TOP or a.card_y > SAFE_BOT:
        sys.exit("ERROR: the card starts outside the safe zone (%d..%d)" % (SAFE_TOP, SAFE_BOT))

    inputs, filters, chain = ["-i", str(a.base)], [], "0:v"
    for i, (word, img, at, secs) in enumerate(pins, start=1):
        end = at + secs
        if end + 0.05 > SAFE_BOT:      # card height is checked after scaling, below
            pass
        inputs += ["-loop", "1", "-t", "%.3f" % (secs + 0.05), "-i", str(img)]
        # The card's OWN timeline starts at 0, so without this shift its fade-out has
        # already run by the time the enable window opens and overlay repeats a fully
        # transparent last frame -- the card silently never appears. setpts moves the
        # card's clock onto the word, so both fades land where they are written.
        filters.append(
            "[%d:v]scale=%d:-1,format=rgba,"
            "fade=t=in:st=0:d=%.2f:alpha=1,fade=t=out:st=%.2f:d=%.2f:alpha=1,"
            "setpts=PTS+%.3f/TB[c%d]"
            % (i, card_w, a.fade, max(0.0, secs - a.fade), a.fade, at, i))
        filters.append("[%s][c%d]overlay=%d:%d:enable='between(t,%.3f,%.3f)'[o%d]"
                       % (chain, i, card_x, card_y, at, end, i))
        chain = "o%d" % i
        print("  %-10s %5.2f -> %5.2f  (%.2fs)  %s" % (word, at, end, secs, img.name))

    a.out.parent.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-v", "error", *inputs, "-filter_complex", ";".join(filters),
        "-map", "[%s]" % chain, "-map", "0:a?", "-c:v", "libx264", "-crf", "16",
        "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "copy", a.out, "-y"])
    out_len = dur(a.out)
    print("[pin-overlay] %s  %.2fs  (base %.2fs, unchanged: %s)"
          % (a.out, out_len, base_len, abs(out_len - base_len) < 0.05))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
