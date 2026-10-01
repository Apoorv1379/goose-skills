// List every skill the site's hero demo can actually run: category pills, five skills each.
//
//   node scripts/list-demo-skills.js [url] > demo-skills.json
//
// The episode picker must choose from THIS list, not from the 120-odd skill pages on the
// site. Only skills in the hero demo's nav have a transcript the screen recorder can play;
// any other skill gets as far as State 2 and dies on "no skill matching ...".
//
// Reads the page the way record-screen-beats.js does, keeping the two traps it already paid
// for: suppress the timed newsletter dialog BEFORE clicking anything (it swallows clicks,
// and every category then reports the same five skills), and wait for the nav to CHANGE
// after a pill click instead of sleeping a fixed time. A category whose labels duplicate
// another's is reported, because that is what a swallowed click looks like.
const { chromium } = require('playwright');
(async () => {
  const url = process.argv[2] || 'https://skills.gooseworks.ai';
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await page.goto(url, { waitUntil: 'networkidle' });
  for (const l of ['Decline', 'Reject', 'Accept']) {
    const b = page.getByRole('button', { name: l });
    if (await b.count()) { await b.first().click().catch(() => {}); break; }
  }
  await page.evaluate(() => setInterval(() =>
    document.querySelectorAll('[role=dialog]').forEach(e => e.remove()), 300));
  const navLabels = () => page.evaluate(() => {
    const nav = document.querySelector('nav.shrink-0');
    return nav ? [...nav.querySelectorAll('button')].map(e => e.innerText.split('\n')[0].trim()) : [];
  });
  const pills = [...new Set(await page.evaluate(() => [...document.querySelectorAll('button')]
    .filter(e => /^(Ads|Lead gen|Research|Content|Competitive intel|SEO|Social)$/.test(e.innerText.trim()))
    .map(e => e.innerText.trim())))];
  const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const out = {};
  let prev = (await navLabels()).join('|');
  for (const c of pills) {
    let labels = [];
    await page.getByRole('button', { name: c, exact: true }).first().click({ timeout: 3000 }).catch(() => {});
    for (let i = 0; i < 30; i++) {
      await page.waitForTimeout(150);
      const now = await navLabels();
      if (now.length && now.join('|') !== prev) { labels = now; break; }
      if (i === 29) labels = now;          // first pill may already be showing
    }
    out[c] = labels.map(l => ({ label: l, slug: slug(l) }));
    prev = labels.join('|');
  }
  const seen = {}; const dupes = [];
  for (const [c, ls] of Object.entries(out)) {
    const k = ls.map(x => x.slug).join('|');
    if (seen[k]) dupes.push(c + ' == ' + seen[k]); else seen[k] = c;
  }
  if (dupes.length) console.error('[list] WARNING duplicate category contents (swallowed click?): ' + dupes.join(', '));
  console.log(JSON.stringify({ url, captured_at: new Date().toISOString(), categories: out }, null, 1));
  await browser.close();
})();
