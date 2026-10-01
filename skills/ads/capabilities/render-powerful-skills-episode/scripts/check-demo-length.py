"""Does the terminal keep printing AFTER the camera finishes its pan?

Run this on the capture BEFORE the wave. It is free, it takes a few seconds, and it is
the one property that separated a usable episode from the one that cost $7.20 and was
thrown away.

  check-demo-length.py <episode>/screen-recording/zoom-edit.mp4 "part 5"

CALIBRATION IS NOT FINISHED. The thresholds below pass the rejected episode when it is
measured unretimed, so treat the output as a measurement to read rather than a gate to
trust. What is reliable is the COMPARISON: run it on episode-2's capture (the shipped
reference) and on the new one, and look at whether the print points keep coming after
the pan. The rejected episode printed at 3.5, 4.0, 4.5 and then nothing until the demo
looped; the shipped one prints every half second to 11.5s.

That is the one property that separated the shipped episodes from the rejected one, and
it is knowable from a free capture before any wave is paid for.

  part 1 shipped : prints at 3.5 .. 10.5, longest still 3.5s   -> good
  part 3 rejected: prints nowhere after the pan, longest still 11.0s -> unusable
"""
import subprocess, sys, tempfile, pathlib
import numpy as np
from PIL import Image

PAN_ENDS = 3.2          # the locked camera settles its last beat at 2.95-4.55s


def profile(src, crop="crop=1080:1100:0:820"):
    d = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vf",
                    crop + ",fps=2,scale=240:-1", str(d / "f%04d.png")], check=True)
    prev, vals = None, []
    for f in sorted(d.glob("*.png")):
        a = np.asarray(Image.open(f).convert("L"), dtype=float)
        if prev is not None:
            vals.append(float(np.abs(a - prev).mean()))
        prev = a
    return vals


def report(src, label):
    vals = profile(src)
    live = [(i + 1) / 2.0 for i, v in enumerate(vals) if v > 0.3 and (i + 1) / 2.0 > PAN_ENDS]
    gaps, run = [], 0
    for v in vals:
        run = run + 1 if v <= 0.3 else 0
        gaps.append(run)
    still = max(gaps) / 2.0 if gaps else 0.0
    verdict = "GOOD" if len(live) >= 6 and still <= 6.0 else "TOO SHORT -- pick another skill"
    print("%-30s prints after the pan at: %s" % (label, ", ".join("%.1f" % t for t in live[:14]) or "NOTHING"))
    print("%-30s longest still %.1fs   -> %s" % ("", still, verdict))
    return verdict.startswith("GOOD")


if __name__ == "__main__":
    ok = report(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else sys.argv[1])
    sys.exit(0 if ok else 1)
