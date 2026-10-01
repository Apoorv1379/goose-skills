// Read what a skill's demo actually does: the command typed and what the run ends on.
//
//   node scripts/skill-transcript.js "Find warm intros" [url] > transcript.json
//
// An episode's four spoken lines must describe the REAL run: VO A names the command the
// viewer types, VO B names what comes back. Until now a person read the demo and typed
// demo_command / demo_result into episode.json by hand -- and, measured, no script ever
// read those fields. This plays the skill in the site's hero demo and returns its settled
// transcript, the command line, and the deliverable chip the run ends on, so the line
// writer works from the run itself.
//
// Navigation and settling follow record-screen-beats.js, including the traps it paid for:
// suppress the timed newsletter dialog before any click, wait for the nav to CHANGE after
// a pill click instead of sleeping, and treat the transcript as ready only once it has
// changed from before the click AND then held still for several reads.
const { chromium } = require('playwright');

(async () => {
  const want = process.argv[2];
  if (!want) {
    console.error('usage: node skill-transcript.js "<skill label or slug>" [url]');
    process.exit(2);
  }
  const url = process.argv[3] || 'https://skills.gooseworks.ai';
  const TERM_SEL = 'div.overflow-hidden.rounded-xl.border';
  const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await page.goto(url, { waitUntil: 'networkidle' });
  for (const l of ['Decline', 'Reject', 'Accept']) {
    const b = page.getByRole('button', { name: l });
    if (await b.count()) { await b.first().click().catch(() => {}); break; }
  }
  await page.evaluate(() => setInterval(() =>
    document.querySelectorAll('[role=dialog]').forEach(e => e.remove()), 300));
  await page.waitForSelector(TERM_SEL, { timeout: 30000 });

  const navLabels = () => page.evaluate(() => {
    const nav = document.querySelector('nav.shrink-0');
    return nav ? [...nav.querySelectorAll('button')].map(e => e.innerText.split('\n')[0].trim()) : [];
  });
  const transcript = () => page.evaluate((s) => {
    const el = document.querySelector(s);
    return el ? el.innerText : '';
  }, TERM_SEL);

  const pills = [...new Set(await page.evaluate(() => [...document.querySelectorAll('button')]
    .filter(e => /^(Ads|Lead gen|Research|Content|Competitive intel|SEO|Social)$/.test(e.innerText.trim()))
    .map(e => e.innerText.trim())))];

  let chosen = null;
  let prev = (await navLabels()).join('|');
  for (const c of pills) {
    await page.getByRole('button', { name: c, exact: true }).first().click({ timeout: 3000 }).catch(() => {});
    let labels = [];
    for (let i = 0; i < 30; i++) {
      await page.waitForTimeout(150);
      const now = await navLabels();
      if (now.length && now.join('|') !== prev) { labels = now; break; }
      if (i === 29) labels = now;
    }
    prev = labels.join('|');
    const hit = labels.find(l => slug(l) === slug(want) || l === want);
    if (hit) { chosen = { category: c, label: hit }; break; }
  }
  if (!chosen) {
    console.error('[transcript] no demo for ' + JSON.stringify(want));
    await browser.close();
    process.exit(1);
  }

  const before = await transcript();
  await page.evaluate((l) => {
    const nav = document.querySelector('nav.shrink-0');
    const b = nav && [...nav.querySelectorAll('button')]
      .find(e => e.innerText.split('\n')[0].trim() === l);
    if (b) b.click();
  }, chosen.label);

  // Changed from before the click, then identical for four consecutive reads.
  let last = before, stable = 0;
  for (let i = 0; i < 240 && stable < 4; i++) {
    await page.waitForTimeout(500);
    const now = await transcript();
    if (now !== before && now === last) stable++; else stable = 0;
    last = now;
  }
  if (stable < 4) {
    console.error('[transcript] the transcript never settled');
    await browser.close();
    process.exit(1);
  }

  const lines = last.replace(/\r/g, '').split('\n').map(s => s.trim()).filter(Boolean);
  // The command is its OWN line starting '/gooseworks '. Matching '/gooseworks' anywhere
  // returned the terminal's path header, '~/dev/gooseworks-app git:(dev)', instead.
  // Any slash command on its own line, not just '/gooseworks'. The site uses a
  // different one per category -- the Ads demo types '/goose-ads ...' -- so matching
  // only '/gooseworks' returned null for it, and write_lines.py refuses to write
  // lines from a transcript with no command.
  const command = lines.find(l => /^\/[a-z][a-z0-9-]*\s+\S/i.test(l)) || null;
  // A run ends on a 'Done. ...' summary (Find leads) OR on a prose summary followed by a
  // follow-up question (Find warm intros ends "Want me to draft warm-intro asks?"). Taking
  // the last line returned that question as the result. So: the 'Done.' line if there is
  // one, else the last line that is real prose -- not a question, not a bullet, not a tool
  // call or a terminal glyph, and long enough to be a sentence rather than a tool's count.
  const isTool = (l) => /^[a-z][\w-]*\(/.test(l) || /^[\u23fa\u23bf>]$/.test(l);
  const isProse = (l) => !/\?\s*$/.test(l) && !/^-\s/.test(l) && !isTool(l) && l.split(/\s+/).length >= 6;
  const result = [...lines].reverse().find(l => /^Done\b/i.test(l))
    || [...lines].reverse().find(isProse) || null;
  const chip = await page.evaluate((s) => {
    const el = document.querySelector(s);
    if (!el) return null;
    const c = [...el.querySelectorAll('span')]
      .filter(x => /rounded/.test(String(x.className)) && /\.[a-z0-9]{2,5}\b/i.test(x.innerText));
    return c.length ? c[c.length - 1].innerText.trim() : null;
  }, TERM_SEL);

  console.log(JSON.stringify({
    skill: chosen.label, category: chosen.category,
    command, result, deliverable_chip: chip,
    last_lines: lines.slice(-6), transcript: lines,
  }, null, 1));
  await browser.close();
})();
