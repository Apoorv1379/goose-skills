#!/usr/bin/env python3
"""Render the results b-roll for a skill whose output is a FILE OF ROWS: leads, prospects,
keywords, mentions, a CSV of anything.

Two data-only treatments exist, and the shape of the output picks between them:

  a written report or summary   -> make-report-broll.py   (a document: headline figures)
  a file with one row per record -> THIS                  (a sheet: rows, scored)

Showing a row-per-record output as a document with two big numbers on it is the wrong
shape: the thing the run made is a list, so the b-roll is the list.

  make-sheet-broll.py --spec sheet.json --vo final/vo-c.mp3 \\
      --out working/results.mp4 [--still review/sheet.png] [--no-sort]

The spec is the file the run actually wrote:

  {
    "file":    "leads/qualified-2026-04-27.csv",
    "filters": "Head of Growth, RevOps - 11-200",
    "count_before": "412 scored",
    "count_after":  "127 hot",
    "columns": ["TITLE", "FUNDING", "HIRING"],
    "score_columns": [1, 2],
    "rows": [ {"cells": ["Head of Growth", "Series A", "growth roles"], "hot": true },
              {"cells": ["RevOps", "Seed", "-"], "hot": false }, ... ]
  }

**Every cell has to come from the run.** For the leads episode that meant the titles the
search used, the size band, and the two enrichment signals it scored on -- and NO company
names, because the run never produced any. Inventing rows to make the sheet look full is
the same lie as using stills from another run (SKILL.md, the payoff rules).

Three beats, one per phrase of VO C, measured off the voice with silencedetect:

  1. the file as written, the count chip reading what went IN
  2. the scoring lands row by row in reading order: unscored rows step back to 20%, the
     scored ones sort to the top, and the chip's label masks up and out while the new one
     masks up and in. It does NOT tick -- a counting number reads as a stat card, which
     this series rejected once already (#122).
  3. the columns it scored ON take a cell tint, the way a selected cell looks in a sheet,
     while the first column eases back.

The palette is terminal.template.html's, exactly. The episode shows a black terminal, so
the payoff is the same screen and never a white one.
"""
import argparse, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

W, H, FPS = 1080, 1920, 30
SAFE_TOP, SAFE_H = 285, 1350            # the 4:5 social safe zone: y 285..1634


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit((r.stderr or r.stdout or "")[-2000:])
    return r


def dur(p: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", p]).stdout.strip())


def beats(vo: Path, n: int) -> list[float]:
    """One start time per beat, taken from the voice's own phrasing.

    Not "the first n silences": a clone often opens with a tenth of a second of lead-in
    and takes a breath mid-clause, and both look identical to a phrase boundary. Part 2's
    VO C opened with 0.13s of silence, which put beat 2 at 0.13s and landed the whole
    scoring pass before the line had said anything. So: the lead-in sets where beat 1
    starts, and the remaining beats come from the LONGEST internal gaps, in time order.
    """
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(vo), "-af",
                        "silencedetect=noise=-32dB:d=0.10", "-f", "null", "-"],
                       capture_output=True, text=True)
    total = dur(vo)
    starts = [float(m) for m in re.findall(r"silence_start: (-?[0-9.]+)", r.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    gaps = list(zip(starts, ends))

    lead = 0.0
    if gaps and gaps[0][0] <= 0.01:
        lead = gaps[0][1]
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


def build_html(spec: dict, marks: list[float], sort: bool) -> str:
    cols = spec.get("columns") or []
    rows = spec.get("rows") or []
    if not cols or not rows:
        sys.exit("ERROR: the spec needs columns and rows.")
    score_cols = spec.get("score_columns")
    if score_cols is None:
        score_cols = list(range(1, len(cols)))
    return TEMPLATE.format(
        file=esc(spec.get("file", "")), filters=esc(spec.get("filters", "")),
        c_before=esc(spec.get("count_before", "")),
        c_after=esc(spec.get("count_after", "")),
        head="".join("<th>%s</th>" % esc(c) for c in cols),
        rows=json.dumps([{"cells": [str(c) for c in r.get("cells", [])],
                          "hot": 1 if r.get("hot") else 0} for r in rows]),
        score_cols=json.dumps(score_cols), sort="true" if sort else "false",
        marks=json.dumps([round(m, 3) for m in marks]),
        safe_top=SAFE_TOP, safe_h=SAFE_H, W=W)


TEMPLATE = r"""<!doctype html><meta charset="utf-8"><title>sheet</title>
<style>
 /* terminal.template.html's palette, exactly: same machine, same screen */
 :root{{--bg:#0f0f0f; --chrome:#1c1c1c; --win:#191919;
       --fg:#d6d3ce; --dim:#8a8681; --faint:#6b6763; --chip:#2a2a28;}}
 *{{box-sizing:border-box}}
 html,body{{margin:0;width:{W}px;height:1920px;background:var(--bg);overflow:hidden}}
 #win{{position:absolute;left:40px;top:{safe_top}px;width:1000px;background:var(--win);
      border-radius:18px;overflow:hidden;box-shadow:0 28px 80px rgba(0,0,0,.6)}}
 #bar{{height:78px;background:var(--chrome);display:flex;align-items:center;
      padding:0 26px;gap:11px}}
 .dot{{width:16px;height:16px;border-radius:50%}}
 #file{{margin-left:24px;font:500 27px "JetBrains Mono",Consolas,monospace;color:var(--dim)}}
 #tools{{display:flex;align-items:center;gap:20px;padding:28px 28px 0}}
 #count{{position:relative;height:60px;min-width:280px;background:var(--chip);
        border-radius:999px;overflow:hidden}}
 #count span{{position:absolute;left:0;right:0;top:0;height:60px;line-height:60px;
        text-align:center;font:600 30px "JetBrains Mono",Consolas,monospace;color:var(--fg)}}
 #sub{{font:500 25px "JetBrains Mono",Consolas,monospace;color:var(--faint)}}
 table{{width:100%;border-collapse:collapse;margin-top:26px}}
 th{{font:600 24px "JetBrains Mono",Consolas,monospace;letter-spacing:.09em;
    color:var(--faint);text-align:left;padding:0 28px 16px;border-bottom:1px solid #262626}}
 td{{font:500 33px "JetBrains Mono",Consolas,monospace;color:var(--fg);padding:21px 28px;
    border-bottom:1px solid #212121;white-space:nowrap}}
 td span{{display:inline-block;padding:4px 14px;margin:-4px -14px;border-radius:8px}}
 .pad{{height:34px}}
</style>
<div id="win">
 <div id="bar">
  <div class="dot" style="background:#ff5f57"></div>
  <div class="dot" style="background:#febc2e"></div>
  <div class="dot" style="background:#28c840"></div>
  <div id="file">{file}</div>
 </div>
 <div id="tools">
  <div id="count"><span id="c0">{c_before}</span><span id="c1">{c_after}</span></div>
  <div id="sub">{filters}</div>
 </div>
 <table><tr>{head}</tr></table>
 <div class="pad"></div>
</div>
<script>
const ROWS = {rows}, SCORE_COLS = {score_cols}, SORT = {sort}, MARKS = {marks};

const tbl = document.querySelector('table');
ROWS.forEach(r => {{
  const tr = document.createElement('tr');
  tr.dataset.hot = r.hot;
  tr.innerHTML = r.cells.map(c => '<td><span>' + c + '</span></td>').join('');
  tbl.appendChild(tr);
}});
const trs = [...tbl.querySelectorAll('tr')].slice(1);
const c0 = document.getElementById('c0'), c1 = document.getElementById('c1');
const ROW_H = trs[0].offsetHeight;

// where each row lands once the list is sorted by score
const order = [...trs.keys()].sort((a, b) =>
  (Number(trs[b].dataset.hot) - Number(trs[a].dataset.hot)) || (a - b));
const target = []; order.forEach((from, to) => {{ target[from] = to; }});

{{ const win = document.getElementById('win');
   win.style.top = Math.round({safe_top} + ({safe_h} - win.offsetHeight) / 2) + 'px'; }}

// The mono fallback sits its cap height high in a 60px line box: measured on the render,
// the ink ran 7px from the top and 32px from the bottom. These labels carry no descender,
// so centring the INK is what reads as centred. 12px is that correction, measured.
const PILL_Y = 12, DIM = 0.20;
const clamp01 = x => x < 0 ? 0 : x > 1 ? 1 : x;
const seg = (t, a, b) => clamp01((t - a) / (b - a));
const p3 = p => 1 - Math.pow(1 - p, 3);
const mix = (a, b, p) => a + (b - a) * p;

function setTime(t){{
  // the count is a toolbar label, so it changes like one: out and up, in and up
  const swap = p3(seg(t, MARKS[1], MARKS[1] + 0.26));
  c0.style.opacity = 1 - swap;
  c0.style.transform = 'translateY(' + (PILL_Y - 26 * swap).toFixed(2) + 'px)';
  c1.style.opacity = swap;
  c1.style.transform = 'translateY(' + (PILL_Y + 26 * (1 - swap)).toFixed(2) + 'px)';

  trs.forEach((tr, i) => {{
    const hot = tr.dataset.hot === '1';
    // BEAT 1: the file writes itself, row by row, while the line says how many were
    // scanned. Without this the first beat is a dead frame -- the whole sheet already
    // sitting there, nothing moving, for a third of the section.
    const w = p3(seg(t, MARKS[0] + i * 0.045, MARKS[0] + i * 0.045 + 0.20));
    // BEAT 2: the scoring lands, in reading order
    const p = p3(seg(t, MARKS[1] + i * 0.05, MARKS[1] + i * 0.05 + 0.24));
    tr.style.opacity = w * (hot ? 1 : mix(1, DIM, p));
    const rise = 12 * (1 - w);
    const slide = SORT ? (target[i] - i) * ROW_H * p : 0;
    tr.style.transform = 'translateY(' + (rise + slide).toFixed(2) + 'px)';

    const q = MARKS.length > 2
      ? p3(seg(t, MARKS[2] + i * 0.04, MARKS[2] + i * 0.04 + 0.24)) : 0;
    // a 3px edge marker on the rows that scored: the cheapest possible state change,
    // and the one a spreadsheet would actually use for a selected row
    tr.children[0].style.boxShadow = (hot && q > 0)
      ? 'inset 3px 0 0 0 rgba(63,185,80,' + (0.85 * q).toFixed(3) + ')' : 'none';
    SCORE_COLS.forEach(col => {{
      const cell = tr.children[col]; if (!cell) return;
      const s = cell.firstChild;
      if (!hot) {{ s.style.background = 'transparent'; s.style.color = ''; return; }}
      s.style.background = q > 0
        ? 'rgba(63,185,80,' + (0.14 * q).toFixed(3) + ')' : 'transparent';
      s.style.color = q > 0.5 ? '#7ee787' : '';
    }});
    tr.children[0].firstChild.style.opacity = hot ? mix(1, 0.55, q) : 1;
  }});
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
    ap.add_argument("--vo", type=Path,
                    help="the results voiceover (VO C). Optional with --still-only: the "
                         "gate asks for the still BEFORE the episode is built, which is "
                         "before the voice exists.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--hold", type=float, default=0.35)
    ap.add_argument("--still", type=Path, help="write the final state as a PNG, to show")
    ap.add_argument("--still-only", action="store_true",
                    help="write --still and stop; no voice needed, no video rendered")
    ap.add_argument("--no-sort", action="store_true",
                    help="leave the scored rows where they are instead of collecting "
                         "them at the top")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()

    if args.still_only and not args.still:
        sys.exit("ERROR: --still-only needs --still.")
    if not args.still_only and not args.vo:
        sys.exit("ERROR: --vo is required unless --still-only.")
    for p in [args.spec] + ([args.vo] if args.vo else []):
        if not p.exists():
            sys.exit("ERROR: missing %s" % p)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    n_beats = 3
    if args.vo:
        marks = beats(args.vo, n_beats)
        length = dur(args.vo) + args.hold
        print("[sheet-broll] beats at %s over %.2fs"
              % (", ".join("%.2f" % m for m in marks), length))
    else:
        marks = [0.02, 1.35, 2.70]
        length = marks[-1] + 1.2

    work = Path(tempfile.mkdtemp(prefix="sheet-broll-"))
    try:
        html = work / "sheet.html"
        html.write_text(build_html(spec, marks, not args.no_sort), encoding="utf-8")
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
            print("[sheet-broll] work kept at %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
