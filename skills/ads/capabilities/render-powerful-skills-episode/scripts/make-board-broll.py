#!/usr/bin/env python3
"""Render the results b-roll as a BOARD: a summary about people or entities.

The third data-only treatment. Which one a skill gets comes from the shape of what the
run wrote:

  a written report or summary     -> make-report-broll.py   (a document)
  a file with one row per record  -> make-sheet-broll.py    (a sheet that scores)
  a summary ABOUT people or orgs  -> THIS                   (a board, no file window)

A board has no file chrome on purpose. The other two show a document the run WROTE; this
one shows what the run FOUND, which does not live in a file the viewer would ever open.
Episode 3 is the case: the run digests three people's LinkedIn activity, and a terminal
printout or a file card both put a window around something that is really just a finding.

  make-board-broll.py --spec board.json --vo final/vo-c.mp3 \\
      --out working/results.mp4 [--still review/board.png] [--still-only]

The spec, every value from the run:

  {
    "kicker":   "PAST 14 DAYS",
    "headline": "47 posts|from 3 people",     # | is a line break
    "rows":     [ {"n": "21", "who": "...", "themes": "..."}, ... ],
    "focus_row": 0,
    "callout":  {"label": "TOP POST", "value": "8.2k reactions", "sub": "..."}
  }

Three beats, one per phrase of VO C, measured off the voice:

  1. the rows arrive in reading order
  2. the row the line NAMES stays lit while the others step back to 28%
  3. the callout rises in

Naming real people is a decision taken per episode (`write_lines.py --allow-names`), and
a board that names them inherits it. Every figure has to come from the run.
"""
import argparse, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

W, H, FPS = 1080, 1920, 30
SAFE_TOP, SAFE_H = 285, 1350


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit((r.stderr or r.stdout or "")[-2000:])
    return r


def dur(p: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", p]).stdout.strip())


def beats(vo: Path, n: int) -> list[float]:
    """One start time per beat, from the voice's own phrasing (see #129)."""
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(vo), "-af",
                        "silencedetect=noise=-32dB:d=0.10", "-f", "null", "-"],
                       capture_output=True, text=True)
    total = dur(vo)
    starts = [float(m) for m in re.findall(r"silence_start: (-?[0-9.]+)", r.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    gaps = list(zip(starts, ends))
    lead = gaps[0][1] if gaps and gaps[0][0] <= 0.01 else 0.0
    internal = [(s, e) for s, e in gaps if s > 0.01 and e < total - 0.05]
    internal.sort(key=lambda g: g[1] - g[0], reverse=True)
    chosen = sorted(internal[:max(0, n - 1)], key=lambda g: g[0])
    marks = [lead] + [e for _, e in chosen]
    if len(marks) < n:
        print("[beats] %d phrase(s) for %d beat(s); spacing the rest evenly"
              % (len(marks), n))
        marks += [total * (i + 1) / (n + 1) for i in range(len(marks), n)]
    return marks[:n]


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_html(spec: dict, marks: list[float]) -> str:
    rows = spec.get("rows") or []
    if not rows:
        sys.exit("ERROR: the spec needs rows.")
    co = spec.get("callout") or {}
    rows_html = "".join(
        '<div class="row"><div class="n">%s</div><div class="col">'
        '<div class="who">%s</div><div class="th">%s</div></div></div>'
        % (esc(r.get("n", "")), esc(r.get("who", "")), esc(r.get("themes", "")))
        for r in rows)
    return TEMPLATE.format(
        kicker=esc(spec.get("kicker", "")),
        headline="".join('<span class="hl"><i>%s</i></span>' % esc(p)
                         for p in str(spec.get("headline", "")).split("|")),
        rows=rows_html,
        callout=('<div id="top"><div class="lab">%s</div><div class="big">%s</div>'
                 '<div class="sub">%s</div></div>'
                 % (esc(co.get("label", "")), esc(co.get("value", "")),
                    esc(co.get("sub", "")))) if co else "",
        focus=(-1 if spec.get("focus_row") is None else int(spec["focus_row"])),
        release=("true" if spec.get("focus_release") else "false"),
        marks=json.dumps([round(m, 3) for m in marks]),
        safe_top=SAFE_TOP, safe_h=SAFE_H, W=W)


TEMPLATE = r"""<!doctype html><meta charset="utf-8"><title>board</title>
<style>
 /* terminal.template.html's palette: the episode is black, so the payoff is too */
 :root{{--bg:#0f0f0f;--fg:#d6d3ce;--dim:#8a8681;--faint:#6b6763;--ok:#7ee787;}}
 *{{box-sizing:border-box}}
 html,body{{margin:0;width:{W}px;height:1920px;background:var(--bg);overflow:hidden;
   font-family:"Segoe UI",Inter,system-ui,sans-serif}}
 #w{{position:absolute;left:0;right:0;top:{safe_top}px;height:{safe_h}px;padding:0 74px;
    display:flex;flex-direction:column;justify-content:center}}
 .kicker{{opacity:0;font:600 28px "JetBrains Mono",Consolas,monospace;letter-spacing:.16em;
   color:var(--faint);margin-bottom:22px}}
 h1{{font:700 76px "Segoe UI",Inter,sans-serif;letter-spacing:-.03em;color:var(--fg);
   margin:0 0 64px;line-height:1.04}}
 .hl{{display:block;overflow:hidden}}
 .hl i{{display:block;font-style:normal;will-change:transform}}
 .row{{display:flex;align-items:baseline;gap:22px;padding:30px 0;
   border-top:1px solid #232323;opacity:0}}
 .n{{font:700 64px "Segoe UI",Inter,sans-serif;letter-spacing:-.03em;color:var(--faint);
   min-width:120px;will-change:transform}}
 .who{{font:600 38px "Segoe UI",Inter,sans-serif;color:var(--fg)}}
 .th{{font:400 27px "JetBrains Mono",Consolas,monospace;color:var(--faint);margin-top:8px}}
 .col{{display:flex;flex-direction:column}}
 #top{{margin-top:58px;opacity:0;border-radius:18px;background:#16181a;padding:38px 42px}}
 #top .lab{{font:600 26px "JetBrains Mono",Consolas,monospace;letter-spacing:.16em;
   color:var(--faint);margin-bottom:14px}}
 #top .big{{font:700 96px "Segoe UI",Inter,sans-serif;letter-spacing:-.04em;
   color:var(--ok);line-height:1}}
 #top .sub{{font:400 32px "Segoe UI",Inter,sans-serif;color:var(--dim);margin-top:10px}}
</style>
<div id="w">
  <div class="kicker">{kicker}</div>
  <h1>{headline}</h1>
  <div id="rows">{rows}</div>
  {callout}
</div>
<script>
// window.top is a read-only global: a `const top` at script scope throws and takes the
// whole file down, so the callout is topBox. Cost one silent render to find.
const rows = [...document.querySelectorAll('.row')];
const hls = [...document.querySelectorAll('.hl i')];
const kicker = document.querySelector('.kicker');
const topBox = document.getElementById('top');
const FOCUS = {focus}, MARKS = {marks}, RELEASE = {release};
const cl = x => x < 0 ? 0 : x > 1 ? 1 : x;
const seg = (t, a, b) => cl((t - a) / (b - a));
const p3 = p => 1 - Math.pow(1 - p, 3);

function setTime(t){{
  // the title masks up a line at a time, so the board opens rather than simply being
  // there. Overflow hidden on the wrapper does the masking; nothing fades.
  const k = p3(seg(t, 0.0, 0.22));
  if (kicker) kicker.style.opacity = k;
  hls.forEach((el, i) => {{
    const p = p3(seg(t, 0.06 + i * 0.09, 0.06 + i * 0.09 + 0.30));
    el.style.transform = 'translateY(' + (100 * (1 - p)).toFixed(2) + '%)';
  }});

  const f = p3(seg(t, MARKS[1], MARKS[1] + 0.24));
  rows.forEach((r, i) => {{
    const p = p3(seg(t, MARKS[0] + i * 0.12, MARKS[0] + i * 0.12 + 0.26));
    r.style.transform = 'translateY(' + (14 * (1 - p)).toFixed(2) + 'px)';
    // focus_row -1 means no row is named: a board of seven metrics has nothing to
    // single out, and dimming six of seven would be a move without a reason.
    // focus_release: the dim LIFTS again on beat 3, for a line shaped like "X did this,
    // and three others changed" -- the others coming back IS what the words say.
    const back = RELEASE ? p3(seg(t, MARKS[2], MARKS[2] + 0.26)) : 0;
    const dim = Math.max(0, (1 - 0.72 * f) + 0.72 * f * back);
    r.style.opacity = p * (FOCUS < 0 || i === FOCUS ? 1 : dim);
    // Beat 2 on a board with nothing to single out: a pass travels down the list, one
    // row at a time, lighting each number as it goes. It is the deck being flipped
    // through, which is what the line is describing -- not decoration.
    if (FOCUS < 0) {{
      const g = p3(seg(t, MARKS[1] + i * 0.11, MARKS[1] + i * 0.11 + 0.20));
      const n = r.querySelector('.n');
      if (n) {{
        n.style.color = g > 0.5 ? 'var(--fg)' : 'var(--faint)';
        n.style.transform = 'translateX(' + (6 * (1 - g)).toFixed(2) + 'px)';
      }}
      r.style.borderTopColor = g > 0.5 ? '#3a3a3a' : '#232323';
    }}
  }});
  if (topBox) {{
    const q = p3(seg(t, MARKS[2], MARKS[2] + 0.30));
    topBox.style.opacity = q;
    topBox.style.transform = 'translateY(' + (18 * (1 - q)).toFixed(2) + 'px)';
  }}
}}
window.setTime = setTime;
setTime(0);
</script>
"""

SHOOT = r"""const { chromium } = require('playwright');
const url = require('url');
const fs = require('fs');
(async () => {
  const [html, outDir, durS, stillAt] = process.argv.slice(2);
  const FPS = 30, N = Math.round(Number(durS) * FPS);
  fs.mkdirSync(outDir, { recursive: true });
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  p.on('pageerror', e => { console.error('PAGE ERROR: ' + e.message); process.exit(3); });
  await p.goto(url.pathToFileURL(html).href);
  await p.waitForTimeout(900);
  if (stillAt) {
    await p.evaluate(t => window.setTime(t), Number(stillAt));
    await p.screenshot({ path: outDir + '/still.png' });
  }
  for (let i = 0; i < N; i++) {
    await p.evaluate(t => window.setTime(t), i / FPS);
    await p.screenshot({ path: `${outDir}/f${String(i).padStart(4, '0')}.png` });
  }
  await b.close();
  console.log('frames ' + N);
})();
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--vo", type=Path, help="the results voiceover (VO C)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--hold", type=float, default=0.35)
    ap.add_argument("--still", type=Path)
    ap.add_argument("--still-only", action="store_true")
    ap.add_argument("--preview", action="store_true",
                    help="render with nominal beats, before VO C exists, so "
                         "the MOTION can be judged. The shipped cut always "
                         "takes its beats from the voice.")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()

    if args.still_only and not args.still:
        sys.exit("ERROR: --still-only needs --still.")
    if not args.still_only and not args.preview and not args.vo:
        sys.exit("ERROR: --vo is required unless --still-only or --preview.")
    for p in [args.spec] + ([args.vo] if args.vo else []):
        if not p.exists():
            sys.exit("ERROR: missing %s" % p)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if args.vo:
        marks = beats(args.vo, 3)
        length = dur(args.vo) + args.hold
        print("[board-broll] beats at %s over %.2fs"
              % (", ".join("%.2f" % m for m in marks), length))
    else:
        marks, length = [0.05, 1.5, 2.9], 4.2

    work = Path(tempfile.mkdtemp(prefix="board-broll-"))
    try:
        html = work / "board.html"
        html.write_text(build_html(spec, marks), encoding="utf-8")
        (work / "shoot.js").write_text(SHOOT, encoding="utf-8")
        frames = work / "frames"
        cmd = ["node", work / "shoot.js", html, frames,
               "0" if args.still_only else "%.3f" % length]
        if args.still:
            cmd.append("%.3f" % (marks[-1] + 0.70))
        sh(cmd)
        if args.still:
            args.still.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(frames / "still.png", args.still)
            print("-> %s   <- SHOW THIS, GET A YES, THEN BUILD THE EPISODE" % args.still)
        if args.still_only:
            return 0
        args.out.parent.mkdir(parents=True, exist_ok=True)
        sh(["ffmpeg", "-v", "error", "-framerate", FPS, "-i", frames / "f%04d.png",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", args.out, "-y"])
        (args.out.parent / (args.out.stem + ".html")).write_text(
            html.read_text(encoding="utf-8"), encoding="utf-8")
        print("-> %s  (%.2fs, silent -- build-episode.py adds the voice)"
              % (args.out, dur(args.out)))
    finally:
        if args.keep_work:
            print("[board-broll] work kept at %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
