#!/usr/bin/env python3
"""Reject takes that mis-say the line, not just ones that add words to it.

`trim-tail.py` matches the transcript against the script to find where the line ends, and
it reports words the model tacked on afterwards. What nothing checked was the line itself:
a take that says "gooseworks-ay-eye" for "gooseworks dot ai", or swallows a syllable, or
substitutes a word, passes every other gate and ships. The operator heard it before any
measurement did.

This aligns the take's transcript to the script word by word and prints the substitutions,
drops and insertions, so a batch can be screened on diction the same way it is screened on
pitch and drift.

  check-diction.py hook-v4/*.mp4 --line "Part one. Powerful Claude skills you should know."

Numbers are normalised both ways ("38" == "thirty eight"), because the transcriber writes
digits where the script spells them out. A take is CLEAN when nothing but that differs.

Environment: FAL_KEY (or FAL_API_KEY).
"""
import argparse, difflib, os, re, subprocess, sys, tempfile
from pathlib import Path

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen \
fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty",
        60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit((r.stderr or r.stdout)[-1200:])
    return r


def spell(n: int) -> list[str]:
    if n < 20:
        return [ONES[n]]
    if n < 100:
        t, r = divmod(n, 10)
        return [TENS[t * 10]] + ([ONES[r]] if r else [])
    if n < 1000:
        h, r = divmod(n, 100)
        return [ONES[h], "hundred"] + (spell(r) if r else [])
    if n < 1_000_000:
        th, r = divmod(n, 1000)
        return spell(th) + ["thousand"] + (spell(r) if r else [])
    return [str(n)]


def words(text: str) -> list[str]:
    # The transcriber writes "42,000" and "3.1" where the script spells them out, so strip
    # thousands separators and read a decimal point as the spoken word "point". Without
    # this every numeric line reads as a mispronunciation and the gate is useless.
    text = re.sub(r"(?<=\d),(?=\d\d\d)", "", text.lower())
    out = []
    for tok in re.findall(r"[A-Za-z0-9']+(?:\.[0-9]+)?", text):
        if re.fullmatch(r"\d+\.\d+", tok):
            whole, frac = tok.split(".")
            out += spell(int(whole)) + ["point"] + [ONES[int(d)] for d in frac]
        elif tok.isdigit() and len(tok) <= 7:
            out += spell(int(tok))
        else:
            out.append(tok)
    return out


def transcribe(clip: Path) -> list[str]:
    from fal_client import subscribe, upload_file
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        sh(["ffmpeg", "-v", "error", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", wav, "-y"])
        r = subscribe("fal-ai/elevenlabs/speech-to-text/scribe-v2",
                      arguments={"audio_url": upload_file(str(wav)), "language_code": "eng"})
    return words(" ".join((w.get("text") or "") for w in ((r or {}).get("words") or [])))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--line", required=True)
    ap.add_argument("--allow-tail", action="store_true",
                    help="ignore anything said AFTER the line (trim-tail removes it)")
    a = ap.parse_args()

    if "FAL_KEY" not in os.environ and "FAL_API_KEY" in os.environ:
        os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]
    if not os.environ.get("FAL_KEY"):
        sys.exit("ERROR: FAL_KEY (or FAL_API_KEY) not set.")

    want = words(a.line)
    clean = []
    for clip in a.clips:
        got = transcribe(clip)
        sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
        faults = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            if a.allow_tail and i1 >= len(want):
                continue
            w, g = " ".join(want[i1:i2]), " ".join(got[j1:j2])
            faults.append({"replace": f"{w!r} -> {g!r}",
                           "delete": f"dropped {w!r}",
                           "insert": f"added {g!r}"}[tag])
        if faults:
            print("  %-22s %s" % (clip.name, "; ".join(faults[:4])))
        else:
            print("  %-22s CLEAN" % clip.name)
            clean.append(clip.name)
    print("\n%d of %d takes say the line exactly." % (len(clean), len(a.clips)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
