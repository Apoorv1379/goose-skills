// Render a Claude Code terminal session to video. No human, no screen recorder.
//
//   node render-terminal.js session.json out/
//
// The session's CONTENT must be a real run of the skill -- paste the actual command,
// the actual tool calls and the actual result. Only the PRESENTATION is rendered.
// That is the whole trick: a screen recording is a picture of something real, and so
// is this, but this one is deterministic and repeatable.
//
// It also writes marks.json: the exact second each step landed, including the
// deliverable. `notify` stops being a hand-pinned guess (which cost episode 4 two
// rebuilds) and becomes a measurement.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const specPath = process.argv[2];
  const outDir = process.argv[3] || 'out';
  if (!specPath) { console.error('usage: node render-terminal.js session.json out/'); process.exit(1); }

  const session = JSON.parse(fs.readFileSync(specPath, 'utf8'));
  session.timing = session.timing || {};
  fs.mkdirSync(outDir, { recursive: true });

  const tpl = path.join(__dirname, 'terminal.template.html');
  const browser = await chromium.launch();
  // 9:16 viewport, and the terminal window is deliberately WIDER than it, so long
  // lines run off the right edge exactly as they do in a hand-zoomed screen recording.
  const W = session.viewport?.width ?? 1080;
  const H = session.viewport?.height ?? 1920;
  const ctx = await browser.newContext({
    viewport: { width: W, height: H },
    deviceScaleFactor: 1,
    recordVideo: { dir: outDir, size: { width: W, height: H } },
  });
  const page = await ctx.newPage();

  // inject the session before the template's script runs
  await page.addInitScript((s) => { window.SESSION = s; }, session);
  await page.goto('file://' + tpl.replace(/\\/g, '/'));

  const t0 = Date.now();
  await page.waitForFunction(() => document.title === 'DONE',
    null, { timeout: (session.timing.timeout_ms ?? 120000) });
  const wall = Date.now() - t0;

  // convert in-page marks to seconds from the first painted frame
  const marks = await page.evaluate(() => window.__marks);
  const base = marks.typing_start ?? 0;
  const seconds = {};
  for (const [k, v] of Object.entries(marks)) seconds[k] = +((v - base) / 1000).toFixed(3);

  await page.waitForTimeout(session.timing.hold_end_ms ?? 1400);
  await ctx.close();
  await browser.close();

  const webm = fs.readdirSync(outDir).filter(f => f.endsWith('.webm')).pop();
  fs.writeFileSync(path.join(outDir, 'marks.json'),
    JSON.stringify({ video: webm, wall_ms: wall, marks_sec: seconds }, null, 1));

  console.log('[render-terminal] wrote ' + path.join(outDir, webm));
  console.log('[render-terminal] typing ends  ' + seconds.typing_end + 's');
  if (seconds.deliverable !== undefined)
    console.log('[render-terminal] DELIVERABLE  ' + seconds.deliverable + 's   <- use this as --notify');
  console.log('[render-terminal] done at     ' + seconds.done + 's');
})();
