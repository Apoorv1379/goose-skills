// Capture the episode's screen plate from the REAL site. No screen recorder, no human.
//
//   node record-screen-beats.js beats.json out/
//
// This records a FLAT plate -- the page at rest, no camera move at all -- plus the
// pixel geometry of the things the camera needs to frame. `apply_camera.py` then does
// the move in post.
//
// That split is deliberate and was arrived at the hard way. Animating the page with a
// CSS transform makes the browser re-layout and re-rasterise the whole document every
// frame; it cannot hold 30fps and the result visibly stutters. Resampling a static
// plate cannot stutter, because nothing is being re-rendered -- which is also why the
// hand-made takes are smooth: they are a camera move over recorded pixels, not a
// browser being asked to redraw itself at speed.
//
// Everything on screen is real: the live site, its real install command, and its own
// embedded terminal demo -- which is what the category pill switches. Only the camera
// is synthetic, and a camera is not content.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const TERM_SEL = spec.terminal_selector || 'div.overflow-hidden.rounded-xl.border';
  const outDir = process.argv[3] || 'out';
  fs.mkdirSync(outDir, { recursive: true });

  // Capture a DESKTOP-width window and let the camera crop 9:16 windows out of it --
  // which is exactly what the hand-made takes are: a 1918x1090 screen recording with
  // portrait crops moving across it. Two reasons it has to be this way:
  //   * recordVideo.size does NOT scale the page to fit, it PADS it. Ask for a video
  //     bigger than the viewport and you get the page in the corner of a grey canvas.
  //     So the only way to more pixels is a bigger viewport, not deviceScaleFactor.
  //   * a phone-width viewport lays the site out small, so a 9:16 crop of it is a 3.5x
  //     upscale. At desktop width the same crop is ~2x, like the reference.
  // The window is taller than a real desktop so the widest beat's crop fits without
  // padding; nothing below the fold is ever framed.
  const W = spec.capture?.width ?? 3840;
  const H = spec.capture?.height ?? 3400;
  // CSS zoom, not deviceScaleFactor. recordVideo captures at the viewport's CSS size,
  // so a higher DSF buys nothing -- it renders at 2x and downsamples straight back.
  // `zoom` divides the effective viewport for layout (3840 at zoom 2 lays out exactly
  // like 1920, same breakpoints, same max-width) while painting everything twice the
  // size, so a 9:16 crop of the result upscales half as much. Beat 2 went from a 3.5x
  // upscale to 1.7x on the same framing.
  const ZOOM = spec.capture?.zoom ?? 2;

  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: W, height: H },
    deviceScaleFactor: 1,
    recordVideo: { dir: outDir, size: { width: W, height: H } },
  });
  const page = await ctx.newPage();
  // The VIDEO clock starts here, with the page -- not at t0 below. Every timestamp
  // handed to the camera must be in this clock. Reporting them relative to a later
  // marker (the category click) shifted the whole take several seconds late: the
  // framing still looked plausible, so the only symptom was the terminal being further
  // through its run than intended and the notify cue landing on the wrong frame.
  const videoT0 = Date.now();
  const vt = () => (Date.now() - videoT0) / 1000;
  // Wait for the thing we actually need, not for the network to fall silent. A site
  // with any polling or streaming never reaches `networkidle`, and on a slow day it
  // times out and takes the whole unattended run down with it. Retry, then assert the
  // demo card is really there.
  let loaded = false;
  for (let attempt = 0; attempt < 3 && !loaded; attempt++) {
    try {
      await page.goto(spec.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForSelector(TERM_SEL, { timeout: 30000 });
      await page.waitForTimeout(1200);
      loaded = true;
    } catch (e) {
      console.error('[record] load attempt ' + (attempt + 1) + ' failed');
    }
  }
  if (!loaded) { console.error('[record] FATAL: could not load ' + spec.url); process.exit(1); }
  if (ZOOM !== 1) {
    await page.evaluate((z) => { document.documentElement.style.zoom = z; }, ZOOM);
    await page.waitForTimeout(600);
  }

  // Consent UI can appear a beat AFTER networkidle, so a single pass right after load
  // misses it and it then sits in every frame of the take. Poll for it, click it, and
  // if it is still there, remove the node -- a banner in the corner of the plate is
  // worse than a slightly impure page.
  const consentGone = async () => !(await page.locator('text=/we use cookies/i').count());
  for (let i = 0; i < 16 && !(await consentGone()); i++) {
    for (const label of ['Decline', 'Reject all', 'Reject', 'Accept']) {
      const b = page.getByRole('button', { name: label, exact: true });
      if (await b.count()) { await b.first().click({ timeout: 1500 }).catch(() => {}); break; }
    }
    await page.waitForTimeout(400);
  }
  if (!(await consentGone())) {
    await page.evaluate(() => {
      for (const e of document.querySelectorAll('div,section,aside'))
        if (/we use cookies/i.test(e.innerText || '') && e.children.length < 8) e.remove();
    });
    console.error('[record] consent UI would not dismiss; removed the node');
  }

  // NO browser chrome is injected here. It used to be, and it was a persistent source
  // of silent failure: the site is a React app, and reconciliation removes DOM it does
  // not own. The bar disappeared PART WAY THROUGH the recording, which also dropped the
  // body padding that had been holding the page down -- so every box measured before
  // that point was 116px stale, and beat 1 framed empty space where an address bar had
  // been. It is a synthetic element anyway, so apply_camera.py draws it in post, where
  // nothing can take it away.
  // Locating an element by its text lands on the innermost node holding that text,
  // which is almost never the thing a viewer would call "that box". Two ways out, and
  // a beat picks one: `climb` walks up a fixed number of parents (the terminal WINDOW
  // is two above its title span; without it you frame the whole card including the
  // skill list beside it), and otherwise we climb to the nearest ancestor that
  // actually paints -- a background, a border, or a corner radius. The site's install
  // command resolves to a bare 200x20 text run; what a viewer sees is the 277x44
  // rounded button around it.
  const FIND = `(a) => {
    const s = a[0], climb = a[1];
    const direct = document.querySelector(s);
    if (direct) return direct;
    let el = [...document.querySelectorAll('button,div,code,pre,span')]
      .find(e => e.innerText && e.innerText.trim().startsWith(s));
    if (!el) return null;
    if (climb) { for (let i = 0; i < climb && el.parentElement; i++) el = el.parentElement; }
    else for (let n = el, i = 0; n && i < 4; n = n.parentElement, i++) {
      const c = getComputedStyle(n);
      if ((c.backgroundColor && c.backgroundColor !== 'rgba(0, 0, 0, 0)') ||
          parseFloat(c.borderTopWidth) > 0 || parseFloat(c.borderTopLeftRadius) > 0) { el = n; break; }
    }
    return el;
  }`;
  const selFor = (b) => ({ __address_bar: '#__chrome .addr',
                           __address_url: '#__chrome .addr .pill' }[b.selector] || b.selector);
  const resolve = (b) => page.evaluate(
    new Function('a', 'const f = ' + FIND + '; const el = f(a); if (!el) return null;' +
      'const r = el.getBoundingClientRect();' +
      // VIEWPORT coordinates, deliberately. The recording is the viewport, not the
      // page, so adding scrollX/scrollY offsets every crop by however far the page
      // happens to be scrolled -- and clicking a pill scrolls it into view. That put
      // every beat on a DIFFERENT terminal further down the page, running a different
      // skill, with no error anywhere. Scrolling is locked below before we measure.
      'return { x: r.x, y: r.y, w: r.width, h: r.height };'),
    [selFor(b), b.climb || 0]);

  // Start the demo on the beat we want it, not on page load. The category pill swaps
  // the site's embedded terminal AND restarts it, so clicking it here is what puts the
  // run mid-stream at the moment the camera pulls out onto it.
  // Keep the page clear, starting BEFORE we click anything. The site pops a newsletter
  // modal on a timer -- a [role=dialog] with a bg-black/50 backdrop -- roughly 25s in.
  // It lands over the terminal and dims everything behind it, which on the first pass I
  // misdiagnosed as the site dimming the terminal when its run completed. It also eats
  // clicks while it is up: with the suppression running after selection instead of
  // before it, every category pill click was swallowed and all seven categories
  // reported the same five skills. Anything on a timer will appear mid-take, so
  // suppress it continuously, and do it first.
  await page.evaluate((sels) => {
    setInterval(() => {
      for (const sel of sels) document.querySelectorAll(sel).forEach(e => e.remove());
    }, 300);
  }, spec.suppress_selectors || ['[role=dialog]']);

  const t0 = Date.now();
  let demoStart = null, restartSec = null, chosen = null;

  // Pick the SKILL, not the category. The site's hero demo is a category pill plus a
  // nav of the five skills in that category, and clicking a skill runs THAT skill's
  // real transcript -- "Technical SEO audit" gives episode 1's
  // `/gooseworks run a technical SEO audit on gooseworks.ai`. So an episode names its
  // skill and nothing else; we find which category holds it. Matching is by slug, so
  // an episode's `featured_skill` ("technical-seo-audit") resolves with no lookup
  // table to maintain -- pass `skill` explicitly only when the two genuinely differ.
  const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const navLabels = () => page.evaluate(() => {
    const nav = document.querySelector('nav.shrink-0');
    return nav ? [...nav.querySelectorAll('button')]
      .map(e => e.innerText.split('\n')[0].trim()) : [];
  });
  const clickSkill = (label) => page.evaluate((l) => {
    const nav = document.querySelector('nav.shrink-0');
    const b = nav && [...nav.querySelectorAll('button')]
      .find(e => e.innerText.split('\n')[0].trim() === l);
    if (!b) return false;
    b.click();
    return true;
  }, label);
  const transcript = () => page.evaluate((sel) => {
    const el = document.querySelector(sel);
    return el ? el.innerText.replace(/\s+/g, ' ') : '';
  }, TERM_SEL);

  if (spec.skill) {
    const want = slug(spec.skill);
    const pills = await page.evaluate(() => [...document.querySelectorAll('button')]
      .filter(e => /^(Ads|Lead gen|Research|Content|Competitive intel|SEO|Social)$/
        .test(e.innerText.trim())).map(e => e.innerText.trim()));
    const order = spec.category ? [spec.category, ...pills.filter(c => c !== spec.category)] : pills;
    // Wait for the nav to actually CHANGE after clicking a pill, rather than a fixed
    // pause. A fixed 700ms passes on a fast run and silently fails on a slow one: every
    // category then reports the previous category's five skills, and the search decides
    // the skill does not exist anywhere.
    const showCategory = async (c) => {
      const before = (await navLabels()).join('|');
      for (let attempt = 0; attempt < 3; attempt++) {
        await page.getByRole('button', { name: c, exact: true }).first()
          .click({ timeout: 3000 }).catch(() => {});
        for (let i = 0; i < 20; i++) {
          await page.waitForTimeout(150);
          const now = await navLabels();
          if (now.length && now.join('|') !== before) return now;
        }
      }
      return await navLabels();
    };
    for (const c of order) {
      const labels = await showCategory(c);
      const hit = labels.find(l => slug(l) === want || l === spec.skill);
      if (hit) { chosen = { category: c, label: hit }; break; }
    }
    if (!chosen) {
      console.error('[record] FATAL: no skill matching ' + JSON.stringify(spec.skill) +
                    ' in any category. Available:');
      for (const c of pills)
        console.error('  ' + c + ': ' + (await showCategory(c)).join(' | '));
      process.exit(1);
    }

    const before = await transcript();
    await clickSkill(chosen.label);
    // Wait for the transcript to CHANGE and then settle. This replaces having to state
    // the expected text per episode: the terminal showing something different from
    // before the click, and then holding still, is the general form of "the right run
    // is up" -- and unlike a DOM text match it cannot pass on content that is present
    // but not yet on screen.
    let last = null, stable = 0;
    for (let i = 0; i < 80; i++) {
      await page.waitForTimeout(250);
      const now = await transcript();
      if (now && now !== before) stable = (now === last) ? stable + 1 : 0;
      last = now;
      if (stable >= 4) break;
    }
    if (stable < 4) { console.error('[record] FATAL: the transcript never settled'); process.exit(1); }
    demoStart = vt();
    console.log('[record] skill "' + chosen.label + '" (category: ' + chosen.category +
                ') up at ' + demoStart.toFixed(2) + 's');
  }

  // Hold the selection for the whole take. Choosing once is not enough: the site
  // advances on its own, so a take that looked right in the first seconds drifts onto
  // a different transcript by the pull-out -- which is where the camera actually
  // lands. Re-select only when it has drifted; that restarts the run from the top,
  // which is what we want on screen anyway.
  if (chosen) await page.evaluate(([cat, label]) => {
    setInterval(() => {
      const pill = [...document.querySelectorAll('button')]
        .find(e => e.innerText.trim() === cat);
      if (pill && !/(^|\s)bg-white(\s|$)/.test(pill.className)) { pill.click(); return; }
      const nav = document.querySelector('nav.shrink-0');
      const b = nav && [...nav.querySelectorAll('button')]
        .find(e => e.innerText.split('\n')[0].trim() === label);
      if (b && !/bg-white|bg-black/.test(b.className)) b.click();
    }, 400);
  }, [chosen.category, chosen.label]);

  // Pin the page to the top and stop it moving, THEN measure. Everything above may
  // have scrolled the page (Playwright scrolls a target into view before clicking it),
  // and a plate that scrolls under a fixed crop is not a camera move, it is a mistake.
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
  });
  await page.waitForTimeout(400);

  const readySec = vt();

  // The DELIVERABLE, located precisely rather than guessed. Every one of the site's
  // transcripts ends by naming the artefact it produced in a chip -- a rounded span
  // with a translucent background and cyan text (`bg-white/10 text-cyan-300`). That is
  // the thing the viewer is meant to click, and #46 is explicit that it is NOT the
  // `Write(...)` tool line above it. Recording WHERE it sits lets the camera time the
  // cue by watching those exact rows of the render, instead of guessing from the last
  // change anywhere on screen -- which lands on a trailing recommendation or a blinking
  // cursor. The transcript is fully laid out by now, so the position is stable across
  // the restart.
  const boxes = {};
  // Wait for the chip rather than sampling once. The transcript settles a beat before
  // its final artefact line paints, so a single read intermittently finds nothing and
  // the whole take loses its payoff framing.
  const readChip = (sel) => page.evaluate((s2) => {
    const t = document.querySelector(s2);
    if (!t) return null;
    const chips = [...t.querySelectorAll('*')].filter(e => !e.children.length &&
      /bg-white\/10/.test((e.className || '').toString()));
    if (!chips.length) return null;
    const r = chips[chips.length - 1].getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height,
             text: chips[chips.length - 1].textContent.trim() };
  }, sel);
  let chip = null;
  for (let i = 0; i < 40 && !chip; i++) {
    chip = await readChip(TERM_SEL);
    if (!chip) await page.waitForTimeout(250);
  }
  if (chip) {
    boxes.deliverable = { x: chip.x, y: chip.y, w: chip.w, h: chip.h };
    console.log('[record] deliverable chip: ' + JSON.stringify(chip.text) +
                ' at y=' + Math.round(chip.y));
  } else {
    console.error('[record] WARNING: no deliverable chip found; the cue will fall back ' +
                  'to the last content change, which is less precise');
  }

  for (const b of spec.beats) {
    const box = await resolve(b);
    if (!box) { console.error('[record] beat target not found: ' + selFor(b)); process.exit(1); }
    if (box.y < 0 || box.y + box.h > H)
      console.error('[record] WARNING: "' + b.label + '" is outside the captured window');
    boxes[b.label] = box;
  }

  // Log every moment the DELIVERABLE appears, in plate time. This is the cue the
  // episode's notification chime lands on, and pinning it by scrubbing the render has
  // cost two rebuilds (critical knowledge #46). The demo restarts, so record every
  // occurrence and let the camera pick the one inside its window.
  // Log every moment the run's LAST line appears, in video time. Kept only as a
  // cross-check: the cue that ships is measured off the render by apply_camera.py,
  // because the DOM holds the whole transcript before any of it is on screen (#74).
  if (spec.deliverable_text) await page.evaluate(([txt, sel, base]) => {
    window.__deliverable = [];
    let had = false, prevLen = 0;
    setInterval(() => {
      const el = document.querySelector(sel);
      const t = el ? el.innerText : '';
      if (t.length < prevLen - 20) had = false;
      prevLen = t.length;
      const now = t.includes(txt);
      if (now && !had) window.__deliverable.push((performance.now() - base) / 1000);
      had = now;
    }, 150);
  }, [spec.deliverable_text, TERM_SEL,
      await page.evaluate(() => performance.now()) - (Date.now() - videoT0)]);

  // Hold the camera on the address bar and the install command first, THEN restart the
  // run, so the terminal is mid-stream when the pull-out reveals it and the deliverable
  // lands during the long final hold. Restarting at the top of the take instead put the
  // whole run inside the first two beats -- measured, the deliverable arrived a second
  // BEFORE the camera had pulled out to show it.
  await page.waitForTimeout((spec.restart_after_sec ?? 2.0) * 1000);

  // Bouncing off a sibling skill forces the restart; clicking the already-selected one
  // does nothing. Without this we join a run already in progress and the deliverable
  // can land before the first frame, which is what makes the notify cue unpinnable.
  if (chosen) {
    const sibling = (await navLabels()).find(l => l !== chosen.label);
    if (sibling) { await clickSkill(sibling); await page.waitForTimeout(250); }
    await clickSkill(chosen.label);
    await page.waitForTimeout(250);
    restartSec = vt();
    console.log('[record] run restarted at ' + restartSec.toFixed(2) + 's');
  }

  // Record for a fixed span AFTER the restart, not to an absolute wall-clock mark.
  // Setup time is not constant -- searching seven categories for a skill takes as long
  // as the site is slow that day -- and an absolute `record_sec` silently leaves too
  // little plate behind the camera. That failed quietly: the camera ran off the end of
  // the plate and wrote a 7.8s take while marks.json still reported the 18.5s it had
  // intended, so the shortfall only showed up when the file was measured.
  const need = (spec.after_restart_sec ?? 40) * 1000 - (Date.now() - (restartSec * 1000 + videoT0));
  if (need > 0) await page.waitForTimeout(need);
  console.log('[record] plate runs to ' + vt().toFixed(1) + 's (' +
              ((vt() - (restartSec ?? 0))).toFixed(1) + 's after the restart)');
  const pageTitle = await page.title();
  const deliverable = spec.deliverable_text
    ? await page.evaluate(() => window.__deliverable || []) : [];
  await ctx.close();
  await browser.close();

  const webm = fs.readdirSync(outDir).filter(f => f.endsWith('.webm')).pop();
  fs.renameSync(path.join(outDir, webm), path.join(outDir, 'plate.webm'));
  fs.writeFileSync(path.join(outDir, 'plate.json'), JSON.stringify({
    plate: 'plate.webm', capture: { width: W, height: H, zoom: ZOOM },
    page_title: pageTitle,
    out: spec.out ?? { width: 1080, height: 1920 },
    demo_start_sec: demoStart, ready_sec: readySec,
    restart_sec: restartSec, deliverable_at_sec: deliverable,
    skill: chosen && chosen.label, category: chosen && chosen.category, boxes,
  }, null, 1));

  console.log('[record] plate  ' + path.join(outDir, 'plate.webm') + '  ' + W + 'x' + H);
  if (demoStart != null) console.log('[record] demo starts at ' + demoStart.toFixed(2) + 's');
  console.log('[record] scroll pinned, boxes measured at ' + readySec.toFixed(2) + 's');
  if (deliverable.length)
    console.log('[record] deliverable appears at ' +
                deliverable.map(v => v.toFixed(2)).join('s, ') + 's (plate time)');
  for (const [k, b] of Object.entries(boxes))
    console.log(`[record] box ${k}: ${Math.round(b.x)},${Math.round(b.y)} ${Math.round(b.w)}x${Math.round(b.h)} css`);
})();
