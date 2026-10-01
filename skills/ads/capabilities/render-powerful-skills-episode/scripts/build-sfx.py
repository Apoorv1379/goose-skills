#!/usr/bin/env python3
"""Build the middle section's SFX bed: three whooshes, one unbroken scan, one notification.

This exists because the bed was hand-assembled twice and both times a cue landed wrong --
once 4.5 seconds early (critical knowledge #11), once at a level that vanished under the
voice (#10, #33). Everything here is derived from MEASUREMENT of the actual edit and VO,
never from typed-in timestamps:

  * Whoosh times come from motion onsets detected in the zoom edit itself.
  * Whoosh GAIN comes from the VO level inside each whoosh's own window. All three are
    loud when the VO has already started; only an unmasked hit gets the quiet setting.
  * The scan runs from the settle of the last zoom to --notify, with passes spaced 2.75s
    (not 2.9s -- the asset is fractionally short and exact spacing leaves audible gaps).
  * The notification goes at --notify, which YOU pin by stepping frames through the edit
    to find where the result appears. The script will not guess it.

Levels, as ratios to the VO peak, holding episode 1's relationships:
    whoosh masked   -9.9 dB      whoosh unmasked  -15.1 dB
    scan           -25.6 dB      notification     -21.6 dB

Usage:
    build-sfx.py --edit zoom-edit.mp4 --vo vo.wav --notify 11.15 --out sfx.wav

The three cues come from the skill's own assets/sfx/ by default; --sfx-dir overrides.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 48000
HERE = Path(__file__).resolve().parent

# Absolute peak targets, measured off episode 1's shipped mix and re-confirmed on episode 2.
WHOOSH_MASKED = 0.620      # -4.2 dB. Was 0.319 (-9.9 dB) through episodes 1-5, and the
                           # operator reported the whooshes as MISSING on two separate
                           # episodes -- at -9.9 dB against a voiceover peaking at
                           # 0.21-0.33 they sit at the same level as the voice and blend
                           # into it instead of cutting through. Measured presence was
                           # never the problem; audibility was (#55).
WHOOSH_CLEAR = 0.177       # -15.1 dB -- nothing to cut through
SCAN = 0.052               # -25.6 dB -- background texture, sits under the voice
NOTIFY = 0.083             # -21.6 dB -- single accent, above the scan bed

GAP = 0.6                  # frames this close belong to the same zoom move
SCAN_SPACING = 2.75        # asset is 2.885s; exact spacing leaves gaps
SCAN_FADE = 0.15           # fade the bed out before the notification so nothing masks it
MASK_THRESHOLD = 0.10      # VO peak in a whoosh window above this = the whoosh is masked


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def load(path: Path) -> np.ndarray:
    """Decode anything to mono float32 at SR."""
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "a.wav"
        sh(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
            "-c:a", "pcm_s16le", str(w), "-y"])
        if not w.exists():
            sys.exit("ERROR: could not decode %s" % path)
        with wave.open(str(w)) as f:
            x = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    return x.astype(np.float32) / 32768.0


def groups_20fps(edit: Path) -> list[tuple[float, float]]:
    """Fallback detector: 20 fps, 240 px wide, threshold at mean + 1 sigma.

    The full-rate 2-sigma detector above found 0 of 3 moves on episode 6 and 1 of 3 on
    episode 10. Simply relaxing its sigma did not fix it -- episode 6 still found nothing
    and episode 10 gained a spurious group at 7.93s. Sampling at 20 fps instead is what
    actually worked, because a zoom spans several frames at that rate and averages into a
    clear plateau, where at 30 fps the per-frame differences are smaller and noisier
    relative to the shake already in the picture. Verified against episodes 6, 7 and 10,
    where it finds the same three moves the operator measured by hand (#69).
    """
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "g20.gray"
        sh(["ffmpeg", "-v", "error", "-i", str(edit), "-vf",
            "fps=20,scale=240:-2,format=gray", "-f", "rawvideo", str(raw), "-y"])
        data = raw.read_bytes()
    w = 240
    h = next((hh for hh in range(380, 460) if len(data) % (w * hh) == 0), None)
    if h is None:
        return []
    n = len(data) // (w * h)
    if n < 4:
        return []
    f = np.frombuffer(data[:n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean((1, 2))
    thr = d.mean() + d.std()
    out: list[tuple[float, float]] = []
    i = 0
    while i < len(d):
        if d[i] > thr:
            j = i
            while j < len(d) and d[j] > thr * 0.5:
                j += 1
            out.append((i / 20.0, min(j, len(d) - 1) / 20.0))
            i = j + 3
        else:
            i += 1
    return out


def motion_groups(edit: Path, expect: int = 3) -> list[tuple[float, float]]:
    """Find the zoom transitions by frame differencing, as (start, end) spans.

    A zoom is a SUSTAINED run of high frame difference lasting ~0.4-0.7s, not a spike, and
    it can dip below threshold mid-move (episode 2's second zoom has a 0.30s internal gap
    between its initial step and its ramp). So group contiguous frames allowing gaps up to
    GAP, and take each group's start. Collapsing by "first frame more than X since the last
    EMITTED one" instead splits a long ramp into phantom extra onsets -- that bug put
    episode 2's third whoosh at 2.40s, in the middle of the second zoom.

    The end of the LAST group is when the picture finally settles, which is where the scan
    bed starts.
    """
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "g.gray"
        sh(["ffmpeg", "-v", "error", "-i", str(edit), "-vf", "scale=192:-1,format=gray",
            "-f", "rawvideo", str(raw), "-y"])
        data = raw.read_bytes()
    fps = 30.0
    w = 192
    h = next((hh for hh in range(300, 400) if len(data) % (w * hh) == 0), None)
    if h is None:
        sys.exit("ERROR: could not infer frame height from the raw stream.")
    n = len(data) // (w * h)
    f = np.frombuffer(data[:n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean((1, 2))

    # RELAX the threshold rather than give up. At 2.0 sigma this found 0 of 3 moves on
    # episode 6 and 1 of 3 on episode 10, while the same grouping at 1.0 sigma found all
    # three cleanly and in the same places both times -- the edits are cut to a template,
    # so the moves are similar in magnitude and a 2-sigma bar can sit above all of them.
    # A missing whoosh is SILENT: the build succeeds and the bed is simply thinner (#69).
    def group_at(k: float):
        thr = d.mean() + k * d.std()
        out: list[tuple[float, float]] = []
        for t in (i / fps for i, v in enumerate(d) if v > thr):
            if out and t - out[-1][1] <= GAP:
                out[-1] = (out[-1][0], t)
            else:
                out.append((t, t))
        return out

    groups = group_at(2.0)
    if len(groups) < expect:
        fb = groups_20fps(edit)
        if len(fb) >= expect:
            print("  motion: %d groups at full rate, %d at 20 fps -- using the latter"
                  % (len(groups), len(fb)))
            groups = fb
    if len(groups) != expect:
        print("WARNING: found %d motion groups, expected %d: %s. Check the edit and pass "
              "--whoosh-times to override."
              % (len(groups), expect, ["%.2f-%.2f" % g for g in groups]), file=sys.stderr)
    return groups


def place(bed: np.ndarray, cue: np.ndarray, t: float, peak: float):
    """Scale cue to an absolute peak and add it at t."""
    i = int(round(t * SR))
    c = cue * (peak / max(float(np.abs(cue).max()), 1e-9))
    j = min(len(bed), i + len(c))
    if j > i:
        bed[i:j] += c[:j - i]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edit", required=True, type=Path, help="the zoom edit (picture)")
    ap.add_argument("--vo", required=True, type=Path, help="this episode's VO, concatenated")
    ap.add_argument("--sfx-dir", type=Path, default=HERE.parent / "assets" / "sfx",
                    help="defaults to the skill's canonical assets/sfx/")
    ap.add_argument("--notify", required=True, type=float,
                    help="the moment the RESULT appears, pinned by stepping frames through "
                         "the edit -- not the tool-call line (critical knowledge #11)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--whoosh-times", help="comma-separated, overrides motion detection")
    args = ap.parse_args()

    whoosh = load(args.sfx_dir / "whoosh-transition.wav")
    scan = load(args.sfx_dir / "digital-text-entry.mp3")
    notif = load(args.sfx_dir / "notification-tone.mp3")
    vo = load(args.vo)

    if args.whoosh_times:
        times = [float(x) for x in args.whoosh_times.split(",")]
        settle = times[-1] + 0.45
    else:
        groups = motion_groups(args.edit)[:3]
        if len(groups) < 3:
            sys.exit("ERROR: found %d motion groups, need 3. A thin bed is silent -- the "
                     "build would succeed and the whooshes would simply be missing. "
                     "Measure the moves and pass --whoosh-times (#69)." % len(groups))
        times = [g[0] for g in groups]
        settle = groups[-1][1]     # the picture stops moving here; the scan starts here
    if not times:
        sys.exit("ERROR: no whoosh times.")

    dur = max(args.notify + len(notif) / SR + 0.2, times[-1] + 1.0)
    bed = np.zeros(int(dur * SR), dtype=np.float32)

    # Whooshes. Gain per cue, from the VO level in that cue's own window (#33).
    print("whooshes:")
    for t in times:
        win = vo[int(t * SR):int((t + 0.6) * SR)]
        vp = float(np.abs(win).max()) if len(win) else 0.0
        masked = vp > MASK_THRESHOLD
        peak = WHOOSH_MASKED if masked else WHOOSH_CLEAR
        place(bed, whoosh, t, peak)
        print("  %5.2fs  VO peak %.3f -> %s  (%.1f dB)"
              % (t, vp, "masked" if masked else "clear", 20 * np.log10(peak)))

    # Scan: unbroken from the settle of the last zoom until the notification.
    scan_start = settle
    t = scan_start
    passes = 0
    while t < args.notify - 0.1:
        place(bed, scan, t, SCAN)
        t += SCAN_SPACING
        passes += 1
    # Fade the bed out just before the notification so nothing masks it.
    f0 = int((args.notify - SCAN_FADE) * SR)
    f1 = int(args.notify * SR)
    bed[f0:f1] *= np.linspace(1.0, 0.0, max(1, f1 - f0), dtype=np.float32)
    bed[f1:] = 0.0
    print("scan:  %.2f - %.2fs, %d passes at %.2fs spacing" % (scan_start, args.notify,
                                                               passes, SCAN_SPACING))

    place(bed, notif, args.notify, NOTIFY)
    # Record the chosen times so build-episode can duck the VOICEOVER under each hit.
    # Raising the whoosh alone cannot fix audibility: measured in episode 5's render the
    # whoosh sat at 0.41x the voice, so it would need ~8 dB more to win, which is harsh.
    # A short dip in the voice makes it punch at a civilised level (#57).
    import json as _json
    (args.out.parent / "whoosh-times.json").write_text(
        _json.dumps([round(float(t), 3) for t in times]), encoding="utf-8")

    print("notification: %.2fs  (%.1f dB)  -- silence after" % (args.notify,
                                                                20 * np.log10(NOTIFY)))

    bed = np.clip(bed, -1.0, 1.0)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.out), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((bed * 32767).astype(np.int16).tobytes())

    print("\n-> %s  (%.2fs)" % (args.out, len(bed) / SR))
    print("Verify every cue in the FINISHED render, not here (critical knowledge #11).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
