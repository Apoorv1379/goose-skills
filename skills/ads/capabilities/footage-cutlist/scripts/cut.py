#!/usr/bin/env python3
"""Render the cut list into the PRODUCT LAYER: a full-size, silent track where every beat's
footage sits in its box and the creator's area is left as a flat plate. Free, local.

    cut.py --cutlist cutlist.json --out layer.mp4 [--draft]

compose-creator-layer then drops the creator into the plate. Rendering the footage first
is deliberate: it is free, and it constrains everything above it, so the user approves the
product layer before any paid take exists.

Framing (per beat `fit`):
  width  fit the whole frame inside the box (by width, or by height if that overflows) and
         fill the rest with `bg`. The default, because footage with copy in it must never
         be cropped: cropping a panel into a narrower aspect slices through the words.
  cover  fill the box and crop the overflow, keeping `focus` [x,y] centred. Only for
         footage whose subject survives the crop.
  crop   cut a source box `crop` [x0,y0,x1,y1] (fractions) first, then fit it by width. For
         a subject that is small or off-centre in a big screen recording.

bg: "auto" samples the footage's own corner colour so the letterbox and the footage read as
one surface and the only visible edge is the seam (a grey or gradient letterbox makes the
footage's own edge look like the boundary). "blur" fills with a darkened blurred copy of
the same frame. "#rrggbb" is a flat colour.

ONE filter graph with the concat FILTER. Separate files joined with the concat demuxer put
black frames at every boundary.
"""
import argparse
import pathlib
import subprocess

from _common import grab, sample_bg, save
from cutlist import box_for, check_or_die

PLATE = "0x1A1A1A"


def even(v):
    return max(2, int(round(v / 2.0)) * 2)


def beat_chain(spec, b, idx, inp, W, H, fps, slot, bgmode):
    """Filter chain producing [s{idx}] (W x H, `slot` seconds) from input number `inp`."""
    y, bh = box_for(spec, b)
    src = spec["_sources"][b["source"]]
    sw, sh = src["width"], src["height"]
    fit = b.get("fit", "width")
    pre = "[%d:v]setpts=(PTS-STARTPTS)/%.6f,fps=%d" % (inp, b["speed"], fps)
    if fit == "crop":
        x0, y0, x1, y1 = b["crop"]
        cw, ch = even((x1 - x0) * sw), even((y1 - y0) * sh)
        pre += ",crop=%d:%d:%d:%d" % (cw, ch, int(x0 * sw), int(y0 * sh))
        sw, sh = cw, ch
        fit = "width"
    if fit == "cover":
        s = max(W / sw, bh / sh)
        rw, rh = even(sw * s), even(sh * s)
        fx, fy = b.get("focus", [0.5, 0.5])
        ox = min(max(int(fx * rw - W / 2), 0), rw - W)
        oy = min(max(int(fy * rh - bh / 2), 0), rh - bh)
        fg = "%s,scale=%d:%d:flags=lanczos,crop=%d:%d:%d:%d,setsar=1[f%d]" % (pre, rw, rh, W, bh, ox, oy, idx)
        body = "[f%d]" % idx
        chains = [fg]
        zone = "color=c=%s:s=%dx%d:r=%d:d=%.3f[z%d]" % (PLATE, W, H, fps, slot, idx)
        chains.append(zone)
        chains.append("[z%d]%soverlay=0:%d:shortest=1,setsar=1[s%d]" % (idx, body, y, idx))
        return chains
    s = min(W / sw, bh / sh)
    rw, rh = even(sw * s), even(sh * s)
    ox, oy = (W - rw) // 2, y + (bh - rh) // 2
    chains = []
    bg = b.get("bg") or bgmode
    if bg == "blur":
        chains.append("%s,split[fa%d][fb%d]" % (pre, idx, idx))
        chains.append("[fa%d]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
                      "gblur=sigma=30,eq=brightness=-0.18,setsar=1[bl%d]" % (idx, W, bh, W, bh, idx))
        chains.append("[fb%d]scale=%d:%d:flags=lanczos,setsar=1[f%d]" % (idx, rw, rh, idx))
        chains.append("color=c=%s:s=%dx%d:r=%d:d=%.3f[z%d]" % (PLATE, W, H, fps, slot, idx))
        chains.append("[z%d][bl%d]overlay=0:%d:shortest=1[zb%d]" % (idx, idx, y, idx))
        chains.append("[zb%d][f%d]overlay=%d:%d:shortest=1,setsar=1[s%d]" % (idx, idx, ox, oy, idx))
        return chains
    col = sample_bg(src["path"], b["in"] + 0.1) if bg == "auto" else "0x" + bg.lstrip("#")
    chains.append("%s,scale=%d:%d:flags=lanczos,setsar=1[f%d]" % (pre, rw, rh, idx))
    chains.append("color=c=%s:s=%dx%d:r=%d:d=%.3f,drawbox=x=0:y=%d:w=%d:h=%d:color=%s:t=fill[z%d]"
                  % (PLATE, W, H, fps, slot, y, W, bh, col, idx))
    chains.append("[z%d][f%d]overlay=%d:%d:shortest=1,setsar=1[s%d]" % (idx, idx, ox, oy, idx))
    b["_bg"] = col
    return chains


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutlist", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--draft", action="store_true", help="fast, half-size, for review only")
    x = ap.parse_args()
    spec = check_or_die(x.cutlist)
    W, H = spec["size"]
    fps = int(spec["fps"])
    cmd = ["ffmpeg", "-v", "error", "-y"]
    chains, labels, n, inp = [], [], 0, 0
    for b in spec["beats"]:
        slot = b["end"] - b["start"]
        if b["state"] == "creator":
            chains.append("color=c=%s:s=%dx%d:r=%d:d=%.3f,setsar=1[s%d]" % (PLATE, W, H, fps, slot, n))
        else:
            src = spec["_sources"][b["source"]]
            cmd += ["-ss", "%.3f" % b["in"], "-t", "%.3f" % (b["out"] - b["in"] + 0.2), "-i", src["path"]]
            chains += beat_chain(spec, b, n, inp, W, H, fps, slot, spec["bg"])
            inp += 1
        chains.append("[s%d]trim=0:%.3f,setpts=PTS-STARTPTS[t%d]" % (n, slot, n))
        labels.append("[t%d]" % n)
        n += 1
    tail = "%sconcat=n=%d:v=1:a=0" % ("".join(labels), n)
    if x.draft:
        tail += ",scale=%d:%d" % (W // 2, H // 2)
    chains.append(tail + ",format=yuv420p[v]")
    enc = ["-preset", "ultrafast", "-crf", "28"] if x.draft else ["-preset", "medium", "-crf", "12"]
    cmd += ["-filter_complex", ";".join(chains), "-map", "[v]", "-an", "-r", str(fps),
            "-c:v", "libx264", *enc, "-movflags", "+faststart", x.out]
    subprocess.run(cmd, check=True)
    layer = {k: v for k, v in spec.items() if not k.startswith("_")}
    layer["layer"] = str(pathlib.Path(x.out).resolve())
    layer["draft"] = bool(x.draft)
    for b in layer["beats"]:
        b.pop("_bg", None)
    save(pathlib.Path(x.out).with_suffix(".json"), layer)
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "csv=p=0", x.out], capture_output=True, text=True).stdout)
    print("[cut] %d beats, %.2fs (cut list %.2fs) %s -> %s  (+ %s)"
          % (n, d, spec["duration"], "DRAFT" if x.draft else "%dx%d" % (W, H), x.out,
             pathlib.Path(x.out).with_suffix(".json").name))


if __name__ == "__main__":
    main()
