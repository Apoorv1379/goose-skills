#!/usr/bin/env python3
"""Move the camera over a recorded plate. This is the edit, not a step before it.

    python scripts/apply_camera.py plate-dir/ camera.json --out zoom-edit.mp4

The plate is the page at rest (record-screen-beats.js). Every zoom here is a crop and
a resample of already-recorded pixels, so it cannot stutter -- unlike animating the
page itself, where the browser re-rasterises the document each frame and drops most
of them.

Two rules, both measured off the finished episodes rather than guessed:

  * a HOLD is dead still. Frame-to-frame difference across every hold in episode 2's
    edit is literally 0.00. No drift, no creep, no slow push.
  * a MOVE is 0.9s, eased in and out, and there are exactly three: in to the address
    bar, across to the install command, then OUT to the running terminal. Nothing
    zooms in after the pull-out.

A beat frames one element. Give it either `fill_w` (element width as a fraction of the
frame) or `fill_h` (its height -- use this for a full-width element like the address
bar, whose width says nothing about the zoom), plus `center`: where in the frame that
element's centre should sit. Anything outside the plate reads as black, which is what
sits above the address bar in the reference.
"""
import argparse, json, math, os, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _font(size, mono=False):
    names = (["consola.ttf", "cour.ttf"] if mono
             else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_chrome(width, url, title=None, bar_h=86, center_url=False):
    """Draw the browser's address bar as an image band, matching the reference.

    Every number here was measured off episode 2's beat-1 frame rather than designed,
    and three of them are the opposite of the obvious guess:

      * there is NO tab strip. The reference shows the address bar with plain black
        above it. Drawing a tab as well puts the site's address on screen twice, which
        no real browser does and which reads as fake at a glance.
      * the URL field is DARKER than the bar it sits in -- rgb(37,36,41) inside
        rgb(59,58,63). Drawing it lighter is what makes a hand-made bar look wrong
        without it being obvious why.
      * the bar is SHORT: 0.065 of frame height at beat 1's zoom, with the URL's cap
        height only a third of the bar. Oversized furniture reads as a mock-up.

    Everything is expressed in bar-heights so the band scales with the plate.
    """
    b = float(bar_h)
    h = int(round(b))
    img = Image.new("RGB", (width, h), (59, 58, 63))
    d = ImageDraw.Draw(img)
    cy = h / 2.0

    # The URL field: darker than the bar, and a rounded RECTANGLE -- not a pill. The
    # corner radius is about a quarter of its height; drawing full semicircular ends is
    # one of the few things that still read as wrong once the colours are right.
    fh = 0.70 * b
    fx0, fx1 = 1.05 * b, width - 0.5 * b
    d.rounded_rectangle([fx0, cy - fh / 2, fx1, cy + fh / 2], 0.32 * fh, fill=(37, 36, 41))

    # Bookmark ribbon, left of the field, sitting on the bar itself. Square-ish top,
    # shallow notch. Drawn as an outlined rounded rect with its bottom edge painted out
    # and replaced by the notch, because PIL has no rounded-polyline outline.
    iw, ih = 0.28 * b, 0.42 * b
    lw = max(1, int(round(0.030 * b)))
    bx, by = 0.50 * b, cy
    x0, y0, x1, y1 = bx - iw / 2, by - ih / 2, bx + iw / 2, by + ih / 2
    d.rounded_rectangle([x0, y0, x1, y1], 0.14 * iw, outline=(198, 196, 201), width=lw)
    d.rectangle([x0 - lw, y1 - 0.34 * ih, x1 + lw, y1 + lw], fill=(59, 58, 63))
    notch = y1 - 0.30 * ih
    d.line([(x0, y1 - 0.36 * ih), (x0, notch), (bx, notch - 0.20 * ih), (x1, notch),
            (x1, y1 - 0.36 * ih)], fill=(198, 196, 201), width=lw, joint="curve")

    # Tune glyph inside the field: two short rules, each with an OUTLINED ring at one
    # end and alternating sides -- ring left on the top rule, ring right on the bottom.
    # Filled dots in the middle of full-width rules is the obvious guess and is wrong.
    gx, gy, g = 1.40 * b, cy, 0.28 * b
    gw = max(1, int(round(0.026 * b)))
    rr = 0.15 * g
    for fy, ring_left in ((-0.30, True), (0.30, False)):
        yy = gy + fy * g
        if ring_left:
            d.ellipse([gx - g / 2 - rr, yy - rr, gx - g / 2 + rr, yy + rr],
                      outline=(184, 182, 189), width=gw)
            d.line([(gx - g / 2 + rr * 1.6, yy), (gx + g / 2, yy)],
                   fill=(184, 182, 189), width=gw)
        else:
            d.line([(gx - g / 2, yy), (gx + g / 2 - rr * 1.6, yy)],
                   fill=(184, 182, 189), width=gw)
            d.ellipse([gx + g / 2 - rr, yy - rr, gx + g / 2 + rr, yy + rr],
                      outline=(184, 182, 189), width=gw)

    # The address itself. The font size is CALIBRATED against the reference: matching the
    # measured cap height suggested 0.46 bar-heights, but the rendered text then came out
    # 1.43x too wide. Set it by the rendered width instead -- 344px of a 125px bar.
    f = _font(int(round(0.325 * b)))
    tw = d.textlength(url, font=f)
    # The band spans the whole PLATE, so a left-aligned URL sits above the page's empty
    # left margin -- and beat 1, which frames the URL, then shows a readable address over
    # 800px of nothing. Safari centres the address, so centring it is honest chrome AND
    # puts the URL on the same x as the page content, which is the only way one crop can
    # hold both.
    tx = (width - tw) / 2 if center_url else 1.94 * b
    d.text((tx, cy), url, font=f, fill=(236, 235, 238), anchor="lm")
    return img, {"address bar": {"x": 0, "y": 0, "w": width, "h": h},
                 "address url": {"x": tx, "y": cy - 0.22 * b, "w": tw, "h": 0.44 * b}}


def last_content_change(path, after_sec, fps, band=None):
    """Second of the last discrete on-screen change after `after_sec`.

    `band` restricts the measurement to a vertical slice of the frame, and it matters:
    the site cycles its hero headline ("Claude Code" / "Cursor" / "Codex") on a timer,
    that headline is still in the top of frame at the final beat, and a whole-frame
    measurement latches onto it instead of the terminal. On one take that put the cue
    at 18.37s of an 18.53s video -- the headline swapping after the payoff had already
    landed. Measure only where the transcript is.
    """
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", "fps=%d,scale=192:341" % fps,
         "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, 341, 192).astype(np.float32)
    if band:
        y0 = max(0, int(band[0] * 341)); y1 = min(341, max(y0 + 8, int(band[1] * 341)))
        a = a[:, y0:y1]
    d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    i0 = int(after_sec * fps) + int(0.3 * fps)
    win = d[i0:]
    if not len(win):
        return None
    # Threshold RELATIVE to the biggest block that lands in this window, not a fixed
    # number. Inside the terminal band a whole new block of output measures ~0.8 while
    # the median frame is 0.002, so any fixed value is either arbitrary or wrong at a
    # different zoom. It also has to be a fraction of the PEAK rather than "any change":
    # once the run finishes the cursor keeps blinking, and taking the last change of any
    # size put the cue 4 seconds after the payoff had already been on screen.
    peak = float(win.max())
    thresh = max(0.5 * peak, 20.0 * float(np.median(win)), 0.02)
    hits = np.flatnonzero(win > thresh)
    return round(float(i0 + hits[-1]) / fps, 3) if len(hits) else None


def load_chrome_asset(path, plate_w, bar_h_plate):
    """The REAL address bar, lifted from the reference recording.

    Drawing one by hand got close but never exact, and there was no need to: the
    finished episodes already contain the bar, filmed. `assets/address-bar.png` is that
    band, cropped at the rows it actually occupies (827-952 of episode 2's beat-1
    frame). Its URL is baked in, which is fine for a series that is always the same
    site -- pass `chrome.draw: true` to fall back to the drawn bar if that ever changes.

    Returned at the asset's NATIVE resolution, widened to cover the plate by repeating
    its last column (which is the field's flat interior, so it extends seamlessly).
    Compositing happens later at OUTPUT resolution: at beat 1 the camera scale works out
    to almost exactly 1.0 against this asset, so the bar on screen is the original's own
    pixels rather than a round trip through the plate's smaller grid.
    """
    src = Image.open(path).convert("RGB")
    need_w = int(math.ceil(plate_w * src.height / float(bar_h_plate)))
    if need_w <= src.width:
        return src.crop((0, 0, need_w, src.height))
    band = Image.new("RGB", (need_w, src.height))
    band.paste(src, (0, 0))
    edge = src.crop((src.width - 1, 0, src.width, src.height))
    band.paste(edge.resize((need_w - src.width, src.height), Image.NEAREST), (src.width, 0))
    return band


def asset_boxes(bar_h):
    """Where the address bar's parts sit, in plate pixels, for the camera to target.

    Same ratios the drawn bar uses, measured off the reference: the URL text starts
    1.94 bar-heights in and runs 2.76 wide, with a cap height of a third of the bar.
    """
    return {"address bar": {"x": 0, "y": 0, "w": 10 ** 6, "h": bar_h},
            "address url": {"x": 1.94 * bar_h, "y": 0.34 * bar_h,
                            "w": 2.76 * bar_h, "h": 0.32 * bar_h}}


def first_content_change(path, after_sec, fps, band):
    """Second at which the given rows of the frame first change materially."""
    d = _row_diff(path, fps, band)
    i0 = int(after_sec * fps) + int(0.3 * fps)
    win = d[i0:]
    if not len(win):
        return None
    peak = float(win.max())
    if peak < 0.02:
        return None
    hits = np.flatnonzero(win > 0.45 * peak)
    return round(float(i0 + hits[0]) / fps, 3) if len(hits) else None


def _row_diff(path, fps, band):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", "fps=%d,scale=192:341" % fps,
         "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, 341, 192).astype(np.float32)
    if band:
        y0 = max(0, int(band[0] * 341)); y1 = min(341, max(y0 + 4, int(band[1] * 341)))
        a = a[:, y0:y1]
    return np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))


def smoothstep(p):
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def cam_for(beat, boxes, W, H):
    """-> (scale, cx, cy) in PLATE pixels. scale = output px per plate px.

    What sets the ZOOM and what gets CENTRED are two different elements, and conflating
    them was an error worth naming. Beat 1 centres the address bar but must take its
    scale from a page element: sizing it off a synthetic browser bar makes the zoom an
    artefact of how tall that bar was drawn. In the reference, beats 1 and 2 sit at
    almost the same zoom -- the move between them is mostly a PAN DOWN, only ~1.2x
    tighter -- which a bar-height reading gets wrong by a factor of 1.5.
    """
    a = boxes[beat.get("scale_on", beat["label"])]
    aw, ah = a["w"], a["h"]
    if "fill_w" in beat:
        s = W * beat["fill_w"] / aw
    elif "fill_h" in beat:
        s = H * beat["fill_h"] / ah
    else:
        raise SystemExit("beat %r needs fill_w or fill_h" % beat["label"])
    c = boxes[beat.get("center_on", beat["label"])]
    # `on` picks the point WITHIN the element; `center` picks where in the frame it
    # goes. An element much wider than the crop (the address bar spans the window, the
    # terminal overflows it) has no useful midpoint -- centring on it frames empty
    # chrome and loses the URL entirely.
    ox, oy = beat.get("on", [0.5, 0.5])
    cx, cy = c["x"] + c["w"] * ox, c["y"] + c["h"] * oy
    fcx, fcy = beat.get("center", [0.5, 0.5])
    # the plate point that must land at (fcx*W, fcy*H) in the output
    return s, cx - (fcx - 0.5) * W / s, cy - (fcy - 0.5) * H / s


def timeline(plan):
    """-> list of (t_start, t_end, from_idx, to_idx) plus total duration."""
    segs, t = [], 0.0
    for i, beat in enumerate(plan["beats"]):
        mv = beat.get("move_sec", 0.0 if i == 0 else 0.9)
        if mv > 0:
            segs.append((t, t + mv, i - 1, i))
            t += mv
        hold = beat.get("hold_sec", 1.0)
        segs.append((t, t + hold, i, i))
        t += hold
    return segs, t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plate_dir")
    ap.add_argument("camera")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()

    pdir = Path(a.plate_dir)
    meta = json.loads((pdir / "plate.json").read_text())
    plan = json.loads(Path(a.camera).read_text())
    W, H = meta["out"]["width"], meta["out"]["height"]      # delivery frame, 9:16
    PW, PH = meta["capture"]["width"], meta["capture"]["height"]  # the desktop plate

    # The chrome band sits ABOVE the plate, so every page box moves down by its height.
    ch_cfg = plan.get("chrome") or {}
    # The bar's height is a fraction of the PLATE's width, so it scales with capture
    # resolution and stays the same size on screen. 0.0224 puts it at 0.065 of the
    # delivery frame at beat 1's zoom, which is what the reference measures.
    CH = int(round(ch_cfg.get("bar_frac", 0.0224) * PW))
    asset_path = ch_cfg.get("asset", str(Path(__file__).resolve().parent.parent /
                                         "assets" / "address-bar.png"))
    use_asset = not ch_cfg.get("draw") and Path(asset_path).exists()
    if use_asset:
        band = load_chrome_asset(asset_path, PW, CH)
        ch_boxes = asset_boxes(CH)
        chrome = None
    else:
        chrome, ch_boxes = build_chrome(
            PW, ch_cfg.get("url", "example.com"),
            ch_cfg.get("title") or meta.get("page_title"), CH,
            bool(ch_cfg.get("center_url")))
        band = None

    boxes = dict(ch_boxes)
    for k, b in meta["boxes"].items():
        boxes[k] = dict(b, y=b["y"] + CH)
    canvas = Image.new("RGB", (PW, PH + CH), (59, 58, 63))
    if chrome is not None:
        canvas.paste(chrome, (0, 0))
    PH += CH

    cams = [cam_for(b, boxes, W, H) for b in plan["beats"]]

    # THE PAYOFF MUST BE IN SHOT -- and the command being run must stay in shot too.
    # The terminal is a fixed-height window whose content fills top-down and never
    # scrolls out of it, so framing the WHOLE terminal guarantees the artefact is
    # visible without chasing it. Sit the terminal's bottom just inside the frame:
    # everything the run produced is then on screen, and the install command and the
    # prompt stay above it exactly where they were.
    #
    # X IS NOT TOUCHED. An earlier version shifted the frame sideways to keep a long
    # file path whole, and that pushed the command itself off the left edge -- trading
    # the thing the episode is about for the thing it ends on. A long path may run off
    # the right; that is how the reference looks too.
    if boxes.get("terminal"):
        ds, dcx, dcy = cams[-1]
        term = boxes["terminal"]
        chh = H / ds
        margin = plan.get("terminal_bottom_margin", 30)
        bottom_aligned = term["y"] + term["h"] + margin - chh / 2
        cams[-1] = (ds, dcx, max(dcy, bottom_aligned))

    # The take opens already moving: the reference's first frame is a wide shot of the
    # page that pushes in over 0.9s. Give it its own framing rather than deriving it by
    # scaling beat 1 about the same point -- that puts the opening on the same spot as
    # beat 1, so the move reads as almost nothing (measured 0.76 mean against the
    # reference's 5.37) and most of the frame is the black above the page.
    if "open" in plan:
        cams.insert(0, cam_for(plan["open"], boxes, W, H))
    else:
        s0, cx0, cy0 = cams[0]
        cams.insert(0, (s0 * plan.get("open_zoom", 0.55), cx0, cy0))
    plan = dict(plan, beats=[dict(plan["beats"][0], move_sec=0, hold_sec=0)] + plan["beats"])
    plan["beats"][1]["move_sec"] = plan["beats"][1].get("open_move_sec", 0.9)

    segs, total = timeline(plan)
    nframes = int(round(total * a.fps))

    # The plate window: the pull-out must land while the terminal is still streaming.
    # Never start before the plate settled: everything up to `ready_sec` is page load,
    # consent, the category click and the scroll being pinned.
    start = plan.get("plate_start_sec")
    if start is None:
        start = (meta.get("ready_sec") or 0.0) + plan.get("settle_sec", 0.3)

    dec = subprocess.Popen(
        # -ss AFTER -i. Input seeking snaps to the nearest keyframe, and the plate's
        # are sparse, so the take would start up to a second from where it was measured
        # -- which is exactly the amount that puts the notify cue on the wrong frame.
        ["ffmpeg", "-v", "error", "-i", str(pdir / meta["plate"]),
         "-ss", "%.3f" % start, "-vf", "fps=%d" % a.fps,
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", "%dx%d" % (W, H), "-r", str(a.fps), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
         a.out], stdin=subprocess.PIPE)

    fsz = PW * (PH - CH) * 3
    marks, seg_i, written = {}, 0, nframes
    for n in range(nframes):
        buf = dec.stdout.read(fsz)
        if len(buf) < fsz:
            print("[camera] FATAL: the plate ran out at frame %d of %d (%.1fs of %.1fs). "
                  "Raise after_restart_sec; a short take here is silent otherwise."
                  % (n, nframes, n / a.fps, nframes / a.fps), file=sys.stderr)
            written = n
            break
        t = n / a.fps
        while seg_i + 1 < len(segs) and t >= segs[seg_i][1]:
            seg_i += 1
        t0, t1, i0, i1 = segs[seg_i]
        if i0 == i1:
            s, cx, cy = cams[i0]
        else:
            e = smoothstep((t - t0) / (t1 - t0))
            (sa, xa, ya), (sb, xb, yb) = cams[i0], cams[i1]
            # interpolate zoom logarithmically: linear scale reads as decelerating
            s = sa * (sb / sa) ** e
            cx, cy = xa + (xb - xa) * e, ya + (yb - ya) * e
            marks.setdefault("beat_%d" % i1, t1)

        cw, ch = W / s, H / s
        x0, y0 = cx - cw / 2, cy - ch / 2
        # Clamp HORIZONTALLY only. There is never page content left or right of the
        # plate, so overrunning sideways just puts black bars down the frame -- but the
        # black ABOVE the plate is deliberate: it is the window furniture the reference
        # shows above the address bar, and it is what lets the bar sit centred.
        if cw <= PW:
            x0 = min(max(x0, 0.0), PW - cw)
        canvas.paste(Image.frombuffer("RGB", (PW, PH - CH), buf, "raw", "RGB", 0, 1), (0, CH))
        frame = np.asarray(canvas)

        # Pad rather than clamp: black above the page is what the reference shows above
        # the address bar, and clamping there would slide the subject off centre.
        ix0, iy0 = int(np.floor(x0)), int(np.floor(y0))
        ix1, iy1 = int(np.ceil(x0 + cw)), int(np.ceil(y0 + ch))
        pad = (max(0, -iy0), max(0, iy1 - PH), max(0, -ix0), max(0, ix1 - PW))
        sub = frame[max(0, iy0):min(PH, iy1), max(0, ix0):min(PW, ix1)]
        if any(pad):
            sub = np.pad(sub, ((pad[0], pad[1]), (pad[2], pad[3]), (0, 0)))
        img = Image.fromarray(sub).resize((W, H), Image.LANCZOS)

        # Composite the real bar HERE, at output resolution, rather than into the plate.
        # Pasting it into the plate would squeeze it through the plate's smaller grid
        # and back out again; done at output size the beat-1 scale lands at ~1.0 and the
        # bar is the reference's own pixels. It is only ever on screen during beat 1 and
        # the opening move -- every later beat crops below it.
        if band is not None:
            oy0, oy1 = (0 - y0) * s, (CH - y0) * s
            if oy1 > 0 and oy0 < H:
                ox0, ox1 = max(0.0, (0 - x0) * s), min(float(W), (PW - x0) * s)
                if ox1 > ox0:
                    # band px per PLATE px. (ox/s + x0) is already a plate
                    # coordinate, so scaling it by an output-space factor magnifies
                    # the bar by exactly the camera's zoom -- which looked like the
                    # asset being wrong rather than the mapping.
                    k = band.height / float(CH)
                    bx0 = (ox0 / s + x0) * k
                    bx1 = (ox1 / s + x0) * k
                    strip = band.crop((int(bx0), 0, max(int(bx0) + 1, int(math.ceil(bx1))),
                                       band.height))
                    tw, th = int(round(ox1 - ox0)), int(round(oy1 - oy0))
                    if tw > 0 and th > 0:
                        img.paste(strip.resize((tw, th), Image.LANCZOS),
                                  (int(round(ox0)), int(round(oy0))))
        enc.stdin.write(img.tobytes())

    enc.stdin.close(); enc.wait(); dec.stdout.close(); dec.wait()
    if written < nframes:
        sys.exit("[camera] refusing to report a take that was not fully written")

    # Convert the deliverable from plate time to TAKE time -- the number `--notify`
    # wants. Pick the first occurrence inside the window we actually filmed.
    dur = written / a.fps
    # Find the deliverable by LOOKING AT THE RENDER, not by asking the page. Two DOM
    # approaches were tried and both reported it ~2.5s early: the site writes the whole
    # transcript into the DOM up front and reveals it by clipping, so `innerText`
    # contains the deliverable long before a viewer can see it, and there is no opacity
    # to test either. On screen the transcript arrives as discrete lines, so the LAST
    # content change during the final hold IS the deliverable landing. Same rule as #46
    # -- pin the cue on the render -- applied by the machine rather than by scrubbing.
    hold_from = max(marks.values()) if marks else 0.0
    fs, fcx, fcy = cams[-1]
    top = fcy - H / fs / 2                       # plate y at the top of the final frame
    rows = lambda bx, pad: (max(0.0, (bx["y"] - pad - top) * fs / H),
                            min(1.0, (bx["y"] + bx["h"] + pad - top) * fs / H))

    # Prefer the deliverable chip's own rows, and take the FIRST change there: that is
    # the frame the artefact appears on. Falling back to the last change anywhere in the
    # terminal is measurably wrong -- it lands on a trailing recommendation line or, once
    # the run finishes, on the blinking cursor, seconds after the payoff.
    # NOTE the camera is NOT moved to suit the cue. Re-anchoring the final beat so the
    # chip always lands at a fixed height was tried and reverted: it changed the framing
    # of the approved take, which is locked. The cue reports None if the chip is not in
    # shot, and #46 has always said the operator owns that judgement.
    if boxes.get("deliverable"):
        notify = first_content_change(a.out, hold_from, a.fps,
                                      rows(boxes["deliverable"], 6))
        if notify is None:
            print("[camera] chip not resolvable in shot; falling back to the last block "
                  "change in the terminal -- CHECK THIS ONE BY EYE (#46)", file=sys.stderr)
            notify = last_content_change(a.out, hold_from, a.fps,
                                         rows(boxes["terminal"], 0) if boxes.get("terminal") else None)
    else:
        notify = last_content_change(a.out, hold_from, a.fps,
                                     rows(boxes["terminal"], 0) if boxes.get("terminal") else None)

    # Trim the tail to the MEASURED cue. How long the site's demo takes varies run to
    # run, so a hand-set hold either cuts the payoff off or leaves dead air after it.
    # Render a generous hold and cut back to the deliverable plus a beat.
    # Never cut before the payoff. An earlier build trimmed to notify + tail while the
    # cue had fired early, and shipped a take that ended before the deliverable appeared
    # at all. Take the later of the cue and the last real change in the terminal.
    tail = plan.get("tail_after_notify_sec")
    if notify is not None:
        last_tx = last_content_change(a.out, hold_from, a.fps,
                                      rows(boxes["terminal"], 0) if boxes.get("terminal") else None)
        if last_tx is not None and last_tx > notify:
            notify = last_tx
    if tail is not None and notify is not None and notify + tail < dur - 0.05:
        dur = notify + tail
        tmp = str(a.out) + ".trim.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.out, "-t", "%.3f" % dur,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                        "-pix_fmt", "yuv420p", "-an", tmp], check=True)
        os.replace(tmp, a.out)
        print("[camera] trimmed to %.2fs (deliverable + %.1fs)" % (dur, tail))

    out = Path(a.out)
    (out.parent / "marks.json").write_text(json.dumps({
        "video": out.name, "duration_sec": round(dur, 3),
        "plate_start_sec": round(start, 3),
        "beats_sec": {k: round(v, 3) for k, v in sorted(marks.items())},
        "notify_sec": notify,
    }, indent=1))
    if notify is not None:
        print("[camera] DELIVERABLE at %.2fs  <- use this as --notify" % notify)
    elif meta.get("deliverable_at_sec"):
        print("[camera] WARNING: the deliverable never appears inside the take", file=sys.stderr)
    print("[camera] %s  %.2fs  %d beats" % (out, nframes / a.fps, len(marks)))
    for k, v in sorted(marks.items()):
        print("[camera]   %s settles at %.2fs" % (k, v))


if __name__ == "__main__":
    main()
