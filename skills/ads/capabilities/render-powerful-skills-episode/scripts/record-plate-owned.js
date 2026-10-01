// Record the site plate with the terminal UNDER OUR CONTROL.
//
//   node record-plate-owned.js session.json out/ [beats.json]
//
// WHY THIS EXISTS
// `record-screen-beats.js` films the site's own demo. That demo loops, so the camera's
// pull-out can land on the PREVIOUS run's finished report (the result appears before the
// command runs), its pace is fixed so the voiceover never lines up, and the only way to
// reach a later moment is to film longer and cut. Five rebuilds of episode 1 died on
// those three things.
//
// Here the page is real -- the real site, the real chrome, the real install command -- but
// the terminal content is ours: the site's terminal is hidden and an identical one is
// placed over it, using the page's own classes so it is pixel-identical, and we append the
// real transcript at times we choose. Nothing loops, nothing is stale, and `deliverable`
// is a fact rather than a detection.
//
// Output matches record-screen-beats.js's contract (plate.webm + plate.json with boxes),
// so `apply_camera.py` runs on it unchanged.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

(async () => {
  const sessionPath = process.argv[2];
  const outDir = process.argv[3] || 'out';
  const beatsPath = process.argv[4];
  if (!sessionPath) { console.error('usage: record-plate-owned.js session.json out/ [beats.json]'); process.exit(1); }

  const session = JSON.parse(fs.readFileSync(sessionPath, 'utf8'));
  const beats = beatsPath ? JSON.parse(fs.readFileSync(beatsPath, 'utf8')) : {};
  const cap = beats.capture || { width: 5760, height: 5100, zoom: 3 };
  // recordVideo.size PADS rather than scales, and deviceScaleFactor buys nothing --
  // the viewport IS the recording (record-screen-beats.js, lines 32-47). More pixels
  // come from a bigger viewport plus CSS zoom, which lays the site out at width/zoom
  // while painting everything zoom times larger.
  console.log('[owned] capture', JSON.stringify(cap));
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: cap.width, height: cap.height },
    deviceScaleFactor: 1,
    recordVideo: { dir: outDir, size: { width: cap.width, height: cap.height } },
  });
  const page = await ctx.newPage();
  // The video clock starts with the page. Every mark handed to apply_camera must be in
  // THIS clock, not in one that starts after setup -- marks past the video's end are the
  // symptom (deliverable 62.19s in a 53.48s plate).
  const videoT0 = Date.now();
  // networkidle times out on this site whenever an analytics socket stays open; wait for
  // the thing we actually need instead of for silence on the network.
  await page.goto(beats.url || 'https://skills.gooseworks.ai',
                  { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForSelector(beats.terminal_selector || 'div.overflow-hidden.rounded-xl.border',
                             { timeout: 90000 });
  // zoom AFTER hydration: set before, and React's first paint resets it, leaving the
  // site laid out at the full viewport width (tiny elements, nothing framed correctly)
  await page.waitForTimeout(6000);
  await page.evaluate((z) => {
    document.documentElement.style.setProperty('zoom', String(z), 'important');
  }, cap.zoom);
  await page.waitForTimeout(1500);

  // dialogs and cookie notices are never part of the shot
  await page.evaluate(() => {
    document.querySelectorAll('[role=dialog]').forEach((d) => d.remove());
    // Only sweep SMALL elements whose OWN text mentions cookies. A greedy version that
    // matched descendant text hid a large ancestor and collapsed the terminal to 0x0,
    // which then silently produced a 6x6 overlay and a useless plate.
    document.querySelectorAll('div,section,aside').forEach((el) => {
      const own = [...el.childNodes].filter((n) => n.nodeType === 3)
        .map((n) => n.textContent).join(' ').toLowerCase();
      const b = el.getBoundingClientRect();
      const small = b.width * b.height < window.innerWidth * window.innerHeight * 0.25;
      if (small && (own.includes('cookie') || own.includes('consent'))) el.style.display = 'none';
    });
    window.scrollTo(0, 0);
    document.documentElement.style.scrollBehavior = 'auto';
  });

  // Hide the site's terminal and put ours in its place, same classes, same box.
  const sel = beats.terminal_selector || 'div.overflow-hidden.rounded-xl.border';
  await page.evaluate(({ sel, header, title, zoom, shape }) => {
    const real = document.querySelector(sel);
    // getBoundingClientRect reports in the ZOOMED space; CSS positioning is in layout
    // px. Place and size the overlay in layout px or it comes out `zoom` times too big.
    let r = real.getBoundingClientRect();
    if (!r.width || !r.height) {
      // something upstream hid it (the cookie sweep is greedy); find a visible one
      const alt = [...document.querySelectorAll(sel)].find((e) => {
        const b = e.getBoundingClientRect(); return b.width > 100 && b.height > 100;
      });
      if (alt) { r = alt.getBoundingClientRect(); real.__alt = true; }
    }
    window.__ownedRect = { w: r.width, h: r.height, zoom };
    const box = { left: r.left / zoom, top: r.top / zoom,
                  width: r.width / zoom, height: r.height / zoom };
    // Hide EVERY terminal the selector matches, not just the first. The site has a
    // second demo lower down, and hiding one left the other running its own Claude
    // session -- invisible under the tight beats, but the wide opening shot showed it,
    // so the episode opened on somebody else's run.
    document.querySelectorAll(sel).forEach((e) => { e.style.visibility = 'hidden'; });
    const mine = document.createElement('div');
    mine.id = '__owned_term';
    mine.className = real.className;
    // The site's terminal is LANDSCAPE; the Reel is 9:16. Framing a 1.3:1 box to fill a
    // portrait frame either crops its sides (the transcript ran off both edges) or leaves
    // 2600px of unrelated page above and below it -- a pink band and a second copy of the
    // terminal were both in shot. Since this terminal is ours, give it a portrait shape:
    // same top, centred on the same x, tall enough to own the frame. Sizes are in CAPTURE
    // px so they can be reasoned about against the plate, then divided into layout px.
    const cx = box.left + box.width / 2;
    const bw = shape ? shape.width / zoom : box.width;
    const bh = shape ? shape.height / zoom : box.height;
    const bl = shape ? cx - bw / 2 : box.left;
    mine.style.cssText = `position:absolute;left:${bl + window.scrollX / zoom}px;top:${box.top + window.scrollY / zoom}px;` +
                         `width:${bw}px;height:${bh}px;z-index:50;visibility:visible;display:flex;flex-direction:column`;
    const fs = shape && shape.font_px ? shape.font_px / zoom : 13;
    mine.innerHTML =
      `<div class="flex items-center justify-between border-b border-white/5 bg-[#2a2a2a] px-3 py-2">` +
        `<div class="flex items-center gap-1.5">` +
          `<span class="h-3 w-3 rounded-full bg-[#ff5f57]"></span>` +
          `<span class="h-3 w-3 rounded-full bg-[#febc2e]"></span>` +
          `<span class="h-3 w-3 rounded-full bg-[#28c840]"></span>` +
        `</div><span class="font-mono text-white/50" style="font-size:${fs*0.85}px">${title}</span><span></span></div>` +
      `<div id="__owned_body" class="flex-1 overflow-y-auto bg-[#1a1a1a] px-4 py-3 font-mono leading-relaxed text-zinc-300" style="font-size:${fs}px">` +
        `<div class="flex items-start gap-2 mb-2"><div class="flex flex-col text-white/60" style="font-size:${fs*0.92}px">` +
          `<span><span class="text-amber-300">claude</span><span class="text-white/40"> ${header.flags}</span></span>` +
          `<span class="text-white/40">${header.version}</span>` +
          `<span class="text-white/40">${header.path}</span>` +
        `</div></div><div class="my-3 h-px bg-white/5"></div>` +
        `<div id="__owned_steps" class="flex flex-col gap-3"></div></div>`;
    document.body.appendChild(mine);
  }, { sel, header: session.header, title: session.title || '',
       zoom: cap.zoom, shape: (beats.terminal_box || null) });

  // Measure NOW, while we know the overlay is on the page and the layout is settled.
  // Measuring at the end read 6x6: by then the site had re-rendered around it.
  const boxes = await page.evaluate(() => {
    const out = {};
    const put = (k, el) => {
      if (!el) return;
      const b = el.getBoundingClientRect();
      out[k] = { x: b.left + window.scrollX, y: b.top + window.scrollY, w: b.width, h: b.height };
    };
    put('terminal', document.getElementById('__owned_term'));
    // EVERY beat is scaled off this box, so what counts as "the install command"
    // decides the zoom of the whole section. record-screen-beats.js -- the recorder the
    // shipped episodes used -- climbs from the text up to the first ancestor that draws
    // something (background, border or radius), i.e. the CHIP, not the text inside it.
    // Taking the inner node instead made the box ~1/3 as wide and every beat ~3x too
    // tight: the reference's opening wide shot became a near-black close-up.
    let install = [...document.querySelectorAll('code,pre,div,span')]
      .filter((e) => /npx gooseworks install/.test(e.textContent || '') && e.children.length <= 3)
      .sort((a, b) => a.textContent.length - b.textContent.length)[0];
    for (let n = install, i = 0; n && i < 4; n = n.parentElement, i++) {
      const c = getComputedStyle(n);
      if ((c.backgroundColor && c.backgroundColor !== 'rgba(0, 0, 0, 0)') ||
          parseFloat(c.borderTopWidth) > 0 || parseFloat(c.borderTopLeftRadius) > 0) {
        install = n; break;
      }
    }
    put('install command', install);
    return out;
  });

  console.log('[owned] terminal rect', JSON.stringify(await page.evaluate(() => window.__ownedRect)));
  const marks = await page.evaluate(async ({ session }) => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const m = { start: performance.now() };
    const steps = document.getElementById('__owned_steps');
    const fontPx = parseFloat(getComputedStyle(document.getElementById('__owned_body')).fontSize);
    const body = document.getElementById('__owned_body');
    await sleep(session.timing?.before_typing ?? 700);

    const row = document.createElement('div');
    row.className = 'flex items-start gap-2';
    row.innerHTML = '<span class="select-none text-emerald-400">&gt;</span>' +
                    '<span class="text-white" id="__typed"></span>';
    steps.appendChild(row);
    m.typing_start = performance.now();
    const typed = document.getElementById('__typed');
    const cps = session.timing?.cps ?? 28;
    for (const ch of session.prompt) {
      typed.textContent += ch;
      await sleep((1000 / cps) * (ch === ' ' ? 1.7 : 0.75 + Math.random() * 0.6));
    }
    m.typing_end = performance.now();
    await sleep(session.timing?.after_typing ?? 600);

    for (const [i, st] of session.steps.entries()) {
      const d = document.createElement('div');
      if (st.type === 'text') {
        d.className = 'whitespace-pre-wrap text-zinc-300';
        d.innerHTML = '<span>' + st.text + '</span>';
      } else if (st.type === 'tool') {
        d.className = 'flex flex-col gap-0.5';
        d.style.fontSize = fontPx * 0.96 + 'px';
        d.innerHTML =
          '<div class="flex items-start gap-2"><span class="select-none text-emerald-400">⏺</span>' +
          '<span class="flex-1 break-words text-zinc-200"><span class="text-zinc-100 font-medium">' + st.name + '</span>' +
          '<span class="text-zinc-500">(' + st.args + ')</span></span></div>' +
          (st.result ? '<div class="flex items-start gap-2 pl-5"><span class="select-none text-zinc-600">⎿</span>' +
                       '<span class="text-zinc-500">' + st.result + '</span></div>' : '');
      } else {
        d.className = 'whitespace-pre-wrap text-zinc-200';
        // Claude Code RENDERS markdown; it does not print the asterisks. A plain
        // text node put `**Last 30 days:**` on screen literally, which is the single
        // clearest tell that a terminal was typed rather than run.
        const md = (t) => t.replace(/\*\*([^*]+)\*\*/g, '<span class="text-white font-semibold">$1</span>')
                           // a backticked path is the RUN'S ARTEFACT, and the closing hold
                           // is the only shot of it. Claude Code draws it as a chip, so
                           // plain emerald text loses the one thing the episode ends on.
                           .replace(/`([^`]+)`/g,
                             '<span style="display:inline-block;border-radius:0.35em;' +
                             'background:rgba(96,165,250,0.16);border:1px solid rgba(96,165,250,0.45);' +
                             'color:#93c5fd;padding:0.05em 0.4em">$1</span>');
        d.innerHTML = st.html || ('<span>' + md(st.text) + '</span>');
      }
      if (st.deliverable) d.id = '__deliverable';
      steps.appendChild(d);
      body.scrollTop = body.scrollHeight;
      if (st.deliverable) m.deliverable = performance.now();
      m['step_' + i] = performance.now();
      await sleep(st.pause ?? 850);
    }
    m.done = performance.now();
    await sleep(session.timing?.hold_end_ms ?? 2500);
    const t0 = m.start;
    for (const k of Object.keys(m)) m[k] = (m[k] - t0) / 1000;
    return m;
  }, { session });
  const playbackStart = (Date.now() - videoT0) / 1000 -
                        (marks.done + (session.timing?.hold_end_ms ?? 2500) / 1000);
  for (const k of Object.keys(marks)) marks[k] += playbackStart;

  const deliverableBox = await page.evaluate(() => {
    const el = document.getElementById('__deliverable');
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return { x: b.left + window.scrollX, y: b.top + window.scrollY, w: b.width, h: b.height };
  });
  if (deliverableBox) boxes.deliverable = deliverableBox;

  await ctx.close();
  await browser.close();
  const webm = fs.readdirSync(outDir).filter((f) => f.endsWith('.webm')).pop();
  fs.writeFileSync(path.join(outDir, 'plate.json'), JSON.stringify({
    plate: webm, capture: cap, out: beats.out || { width: 1080, height: 1920 },
    owned: true, marks_sec: marks,
    deliverable_at_sec: marks.deliverable != null ? [marks.deliverable] : [],
    skill: session.skill || null, boxes,
  }, null, 1));
  console.log('[owned] ' + path.join(outDir, webm));
  console.log('[owned] typing ends %ss, deliverable %ss, done %ss',
    (marks.typing_end || 0).toFixed(2), (marks.deliverable || 0).toFixed(2), marks.done.toFixed(2));
})();
