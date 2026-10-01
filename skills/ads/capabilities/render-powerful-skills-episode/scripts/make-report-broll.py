#!/usr/bin/env python3
"""Render the results b-roll for a DATA-only skill: the report the run wrote.

The payoff for a skill whose output is data -- an audit, a table, a performance report --
is the document itself, full frame. Not the terminal's last frame held (that is a freeze,
not b-roll) and not figures animated on black (no real surface; it reads generic). Both
were tried on @gooseaitools part 1 and rejected. See SKILL.md #122.

  make-report-broll.py --spec report.json --vo final/vo-c.mp3 \\
      --out working/results.mp4 [--still review/report.png] [--hold 0.35]

The spec is the report's CONTENT, which must be the real output of the real run:

  {
    "file":   "reports/meta-performance-2026-06-23.md",
    "title":  "Meta ad performance",
    "meta":   "gooseworks-app - last 30 days - 38 ads",
    "figures": [ {"label": "SPEND", "value": "$42,318"},
                 {"label": "RETURN ON AD SPEND", "value": "3.1x"} ],
    "table":  { "title": "FATIGUED CREATIVES",
                "columns": ["AD", "FREQ", "CTR"],
                "rows": [ ["founders-ugc-03", "4.8", "-41%"], ... ] }
  }

Every column after the first is right-aligned, and the last one is treated as the delta
and given the section's single accent colour.

Motion, and the reasons for it (the series taste file: one idea per beat, calm eases,
hard cuts, nothing decorative):

  * ONE beat per phrase of the voiceover, and the phrases are measured off the voice
    itself with silencedetect -- no transcription call, because the moves land on
    phrases, not on individual words.
  * Each figure takes its turn: it comes up out of grey while the others DIM to 32%.
    Focus is carried by dimming, never by adding a highlight or a keyline.
  * The final phrase builds the table: rows in reading order, 0.08s apart, a 0.22s fade
    with a 10px rise. No bounce, no scale, no count-up.
  * The camera never moves. Then it holds dead still to the end.

Frames are rendered ONE AT A TIME from a pure function of time (window.setTime), not
captured with recordVideo: a recording drifts a frame or two per second against the
voice, which is exactly what makes motion read as running NEXT to the line (#121).
"""
import argparse, json, math, re, shutil, subprocess, sys, tempfile
from pathlib import Path

W, H, FPS = 1080, 1920, 30
SAFE_TOP, SAFE_BOT = 285, 1634          # the 4:5 social safe zone, same as make-caption.py
BAR_H = 78


def sh(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
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
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_html(spec: dict, marks: list[float]) -> str:
    figs = spec.get("figures") or []
    tab = spec.get("table") or {}
    cols = tab.get("columns") or []
    rows = tab.get("rows") or []

    fig_html = "".join(
        '  <div class="lab">%s</div>\n  <div class="val" id="v%d">%s</div>\n'
        % (esc(f.get("label", "")), i, esc(f.get("value", "")))
        for i, f in enumerate(figs))
    head = "".join('<th%s>%s</th>' % (' class="n"' if i else "", esc(c))
                   for i, c in enumerate(cols))
    def cell(i, c):
        # the accent is for a DELTA, not for "the last column". On a table whose final
        # column is a source or a label, colouring it red reads as a warning about
        # something that is not a warning.
        s = str(c).strip()
        delta = bool(re.match(r"^[+-]", s)) or s.endswith("%")
        cls = (" class=\"n d\"" if i and delta else " class=\"n\"" if i else "")
        return "<td%s>%s</td>" % (cls, esc(c))

    body = "".join('<tr class="row">%s</tr>'
                   % "".join(cell(i, c) for i, c in enumerate(r))
                   for r in rows)
    table_html = ("" if not rows else
                  '  <h2 id="h2">%s</h2>\n  <table><tr>%s</tr>%s</table>\n'
                  % (esc(tab.get("title", "")), head, body))

    return TEMPLATE.format(
        file=esc(spec.get("file", "")), title=esc(spec.get("title", "")),
        meta=esc(spec.get("meta", "")), figures=fig_html, table=table_html,
        n_fig=len(figs), marks=json.dumps([round(m, 3) for m in marks]),
        safe_top=SAFE_TOP, safe_h=SAFE_BOT - SAFE_TOP, bar_h=BAR_H, W=W)


TEMPLATE = r"""<!doctype html><meta charset="utf-8"><title>report</title>
<style>
 /* terminal.template.html's palette, exactly: the episode shows a black terminal, so
    the payoff is the same screen. A white card in the middle of it flashes. */
 :root{{--bg:#0f0f0f; --chrome:#1c1c1c; --win:#191919;
       --fg:#d6d3ce; --dim:#8a8681; --faint:#6b6763; --chip:#2a2a28;}}
 *{{box-sizing:border-box}}
 html,body{{margin:0;width:{W}px;height:1920px;background:var(--bg);overflow:hidden}}
 #win{{position:absolute;left:40px;top:{safe_top}px;width:1000px;background:var(--win);
      border-radius:18px;overflow:hidden;box-shadow:0 28px 80px rgba(0,0,0,.6)}}
 #bar{{height:{bar_h}px;background:var(--chrome);display:flex;
      align-items:center;padding:0 26px;gap:11px}}
 .dot{{width:16px;height:16px;border-radius:50%}}
 #file{{margin-left:24px;font:500 27px "JetBrains Mono",Consolas,monospace;color:var(--dim)}}
 #view{{position:relative;width:1000px;overflow:hidden}}
 .pad{{padding:54px 58px 60px}}
 h1{{font:700 68px "Segoe UI",Inter,system-ui,sans-serif;letter-spacing:-.025em;
    color:var(--fg);margin:0 0 14px}}
 .meta{{font:400 29px "JetBrains Mono",Consolas,monospace;color:var(--dim);margin:0 0 62px}}
 .lab{{font:600 30px "JetBrains Mono",Consolas,monospace;letter-spacing:.14em;
      color:var(--dim);margin:0 0 10px}}
 .val{{font:700 156px "Segoe UI",Inter,system-ui,sans-serif;letter-spacing:-.04em;
      color:var(--fg);line-height:1;margin:0 0 56px}}
 h2{{font:600 30px "JetBrains Mono",Consolas,monospace;letter-spacing:.14em;
    color:var(--faint);margin:0 0 30px}}
 table{{width:100%;border-collapse:collapse}}
 th{{font:600 26px "JetBrains Mono",Consolas,monospace;letter-spacing:.1em;color:var(--faint);
    text-align:left;padding:0 0 18px;border-bottom:1px solid #262626}}
 th.n,td.n{{text-align:right}}
 td{{font:500 44px "JetBrains Mono",Consolas,monospace;color:var(--fg);padding:26px 0;
    border-bottom:1px solid #212121}}
 td.d{{color:#ff7b6b}}
 tr.row{{opacity:0}}
</style>
<div id="win">
 <div id="bar">
  <div class="dot" style="background:#ff5f57"></div>
  <div class="dot" style="background:#febc2e"></div>
  <div class="dot" style="background:#28c840"></div>
  <div id="file">{file}</div>
 </div>
 <div id="view"><div class="pad">
  <h1>{title}</h1>
  <div class="meta">{meta}</div>
{figures}{table} </div></div>
</div>
<script>
// Every property is a pure function of t, so the render is seek-safe: frame N comes from
// setTime(N/30) and nothing carries over between frames.
const DIM = 0.32, N_FIG = {n_fig}, MARKS = {marks};
const clamp01 = x => x < 0 ? 0 : x > 1 ? 1 : x;
const seg = (t, a, b) => clamp01((t - a) / (b - a));
const power3Out = p => 1 - Math.pow(1 - p, 3);
const mix = (a, b, p) => a + (b - a) * p;

const vals = [...Array(N_FIG).keys()].map(i => document.getElementById('v' + i));
const h2   = document.getElementById('h2');
const rows = [...document.querySelectorAll('tr.row')];
const TABLE_AT = MARKS.length > N_FIG ? MARKS[N_FIG] : null;

// the window is sized to its own content and centred in the safe zone, so the frame
// never carries a dead strip under the document
{{
  const win = document.getElementById('win'), view = document.getElementById('view');
  const h = Math.min({safe_h}, {bar_h} + view.offsetHeight);
  win.style.height = h + 'px';
  win.style.top = Math.round({safe_top} + ({safe_h} - h) / 2) + 'px';
  view.style.height = (h - {bar_h}) + 'px';
}}

function setTime(t){{
  // each figure takes its turn, and the one before it steps back. Nothing appears.
  vals.forEach((el, i) => {{
    if (!el) return;
    const inP  = power3Out(seg(t, MARKS[i] + 0.02, MARKS[i] + 0.20));
    const next = (i + 1 < MARKS.length) ? MARKS[i + 1] : null;
    const outP = next === null ? 0 : power3Out(seg(t, next, next + 0.18));
    el.style.opacity = mix(mix(DIM, 1, inP), DIM, outP);
  }});
  if (h2 && TABLE_AT !== null)
    h2.style.opacity = mix(DIM, 1, power3Out(seg(t, TABLE_AT, TABLE_AT + 0.25)));

  // the final phrase builds the table, in reading order
  const start = TABLE_AT === null ? 0 : TABLE_AT + 0.30;
  rows.forEach((r, i) => {{
    const p = power3Out(seg(t, start + i * 0.08, start + i * 0.08 + 0.22));
    r.style.opacity = p;
    r.style.transform = 'translateY(' + (10 * (1 - p)).toFixed(2) + 'px)';
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
    ap.add_argument("--spec", required=True, type=Path, help="the report's content, JSON")
    ap.add_argument("--vo", type=Path,
                    help="the results voiceover (VO C). Optional with --still-only: the "
                         "approval still is the FINAL state, which does not depend on "
                         "timing, and the gate says show it BEFORE the episode is built, "
                         "which is before the voice exists.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--hold", type=float, default=0.35,
                    help="still hold after the line ends (default 0.35)")
    ap.add_argument("--still", type=Path,
                    help="also write the FINAL state as a PNG, for approval before the "
                         "render is put in front of anyone")
    ap.add_argument("--still-only", action="store_true",
                    help="write --still and stop; no voice needed, no video rendered")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ERROR: ffmpeg not on PATH.")
    if args.still_only and not args.still:
        sys.exit("ERROR: --still-only needs --still.")
    if not args.still_only and not args.vo:
        sys.exit("ERROR: --vo is required unless --still-only.")
    for p in [args.spec] + ([args.vo] if args.vo else []):
        if not p.exists():
            sys.exit("ERROR: missing %s" % p)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    n_fig = len(spec.get("figures") or [])
    has_table = bool((spec.get("table") or {}).get("rows"))
    n_beats = n_fig + (1 if has_table else 0)
    if not n_beats:
        sys.exit("ERROR: the spec has neither figures nor table rows.")

    if args.vo:
        marks = beats(args.vo, n_beats)
        length = dur(args.vo) + args.hold
        print("[report-broll] %d beat(s) at %s over %.2fs"
              % (n_beats, ", ".join("%.2f" % m for m in marks), length))
    else:
        # nominal spacing: the still is the end state, so the marks only have to be passed
        marks = [i * 1.2 for i in range(n_beats)]
        length = marks[-1] + 1.0

    work = Path(tempfile.mkdtemp(prefix="report-broll-"))
    try:
        html = work / "report.html"
        html.write_text(build_html(spec, marks), encoding="utf-8")
        (work / "shoot.js").write_text(SHOOT, encoding="utf-8")
        frames = work / "frames"
        shoot = ["node", work / "shoot.js", html, frames,
                 "0" if args.still_only else "%.3f" % length]
        if args.still:
            shoot.append("%.3f" % (marks[-1] + 0.60))
        sh(shoot)

        if args.still_only:
            args.still.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(frames / "still.png", args.still)
            print("-> %s   <- SHOW THIS, GET A YES, THEN BUILD THE EPISODE" % args.still)
            return 0

        args.out.parent.mkdir(parents=True, exist_ok=True)
        sh(["ffmpeg", "-v", "error", "-framerate", FPS, "-i", frames / "f%04d.png",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", args.out, "-y"])
        if args.still:
            args.still.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(frames / "still.png", args.still)
            print("-> %s   <- SHOW THIS BEFORE THE EPISODE IS RENDERED" % args.still)
        print("-> %s  (%.2fs, silent -- build-episode.py adds the voice)"
              % (args.out, dur(args.out)))
        # the keyframe copy the html came from, so a re-render is reproducible
        (args.out.parent / (args.out.stem + ".html")).write_text(
            html.read_text(encoding="utf-8"), encoding="utf-8")
    finally:
        if args.keep_work:
            print("[report-broll] work kept at %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
