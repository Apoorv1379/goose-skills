#!/usr/bin/env python3
"""Join any number of creator takes into ONE continuous creator track. No human.

    join_takes.py --spec work/takes/takes.json --end 29.3 --out creator.mp4
    join_takes.py --take t1.mp4:0 --take t2.mp4:10.86 --end 29.3 --out creator.mp4

`file:reel_start` is where that take's first word belongs in the reel (takes.json's
`covers[0]`). --spec reads every take and its start from takes.json. --end is the reel
length (the last beat's end). Free, local.

Every choice below was measured on the reference builds:
  - a 0.10s picture cross-dissolve (0.20s ghosted two pairs of hands; a hard cut is visible)
  - each later take starts LEAD=0.11s early, inside the silence before its first word, so the
    picture dissolves while the audio is still clean
  - a matching acrossfade on the audio
  - ONE filter graph with the concat FILTER: separate files joined with the concat demuxer put
    two black frames at every boundary, and xfade given a late offset silently concatenated
    instead of overlapping. Every xfade here has offset 0, on two XF-long trims.
"""
import argparse
import subprocess

XF = 0.10
LEAD = 0.11


def onset(p, noise="-35dB"):
    """Seconds of silence before a take's first word. D1's take 2 measured 0.116s, which is
    where LEAD came from; a new take will not match it, so measure each one."""
    err = subprocess.run(["ffmpeg", "-v", "info", "-t", "3", "-i", p, "-af",
                          "silencedetect=noise=%s:d=0.03" % noise, "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    import re
    m = re.search(r"silence_start: (-?[0-9.]+)[\s\S]*?silence_end: ([0-9.]+)", err)
    if m and float(m.group(1)) <= 0.02:
        return float(m.group(2))
    return 0.0


def length(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", p], capture_output=True, text=True).stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", action="append", help="file:reel_start_seconds")
    ap.add_argument("--spec", help="takes.json from plan_takes.py (instead of --take)")
    ap.add_argument("--end", type=float, required=True, help="reel length")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    if a.spec:
        import json, pathlib
        sp = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
        takes = [(str(pathlib.Path(sp["out"]) / ("%s-seed%d.mp4" % (t["id"], t["seed"]))), t["covers"][0])
                 for t in sp["takes"]]
        for p, _ in takes:
            if not pathlib.Path(p).exists():
                raise SystemExit("take missing: %s (run run_takes.py)" % p)
    elif a.take:
        takes = [(t.rsplit(":", 1)[0], float(t.rsplit(":", 1)[1])) for t in a.take]
    else:
        raise SystemExit("give --spec or --take")
    takes.sort(key=lambda t: t[1])
    # reel time of each take's first frame
    # A later take starts early by its own measured silence (at least XF, so the dissolve
    # never covers a word), falling back to D1's LEAD when the take opens on sound.
    leads = [max(XF, onset(p) or LEAD) for p, _ in takes]
    r = [0.0] + [max(0.0, s - leads[k + 1]) for k, (_, s) in enumerate(takes[1:])]
    n = len(takes)
    if n == 1:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", takes[0][0], "-t", "%.3f" % a.end,
                        "-r", str(a.fps), "-c:v", "libx264", "-crf", "10", "-c:a", "aac",
                        "-b:a", "192k", a.out], check=True)
        print("[join] one take, trimmed to %.2fs -> %s" % (a.end, a.out))
        return

    fc, pieces, audio = [], [], []
    for k, (path, _) in enumerate(takes):
        e = (r[k + 1] - r[k]) if k + 1 < n else (a.end - r[k])
        need = e + (XF if k + 1 < n else 0.0)
        have = length(path)
        if have + 1e-3 < need:
            raise SystemExit("%s is %.2fs but must run %.2fs to reach its join; plan a longer take"
                             % (path, have, need))
        b = XF if k > 0 else 0.0
        fc.append("[%d:v]trim=%.3f:%.3f,setpts=PTS-STARTPTS[body%d]" % (k, b, e, k))
        pieces.append("[body%d]" % k)
        if k + 1 < n:
            fc.append("[%d:v]trim=%.3f:%.3f,setpts=PTS-STARTPTS[xa%d]" % (k, e, e + XF, k))
            fc.append("[%d:v]trim=0:%.3f,setpts=PTS-STARTPTS[xb%d]" % (k + 1, XF, k))
            fc.append("[xa%d][xb%d]xfade=transition=fade:duration=%.2f:offset=0[x%d]" % (k, k, XF, k))
            pieces.append("[x%d]" % k)
        fc.append("[%d:a]atrim=0:%.3f,asetpts=PTS-STARTPTS[a%d]" % (k, need, k))
        audio.append("[a%d]" % k)
    fc.append("%sconcat=n=%d:v=1:a=0,setsar=1[v]" % ("".join(pieces), len(pieces)))
    prev = audio[0]
    for k in range(1, n):
        lab = "[ac%d]" % k
        fc.append("%s%sacrossfade=d=%.2f:c1=tri:c2=tri%s" % (prev, audio[k], XF, lab))
        prev = lab
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for path, _ in takes:
        cmd += ["-i", path]
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", prev, "-r", str(a.fps),
            "-c:v", "libx264", "-crf", "10", "-c:a", "aac", "-b:a", "192k", a.out]
    subprocess.run(cmd, check=True)
    print("[join] %d takes, joins at %s, %.2fs -> %s  (measured %.2fs)"
          % (n, ", ".join("%.2f" % x for x in r[1:]), a.end, a.out, length(a.out)))


if __name__ == "__main__":
    main()
