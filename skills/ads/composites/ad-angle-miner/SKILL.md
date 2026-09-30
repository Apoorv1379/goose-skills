---
name: ad-angle-miner
description: >
  Find the ad angles worth running, then turn them into what the user asked for: copy angles
  only, video ad ideas, or static ad ideas. Mines customer language (reviews, Reddit, social
  comments, support tickets) and competitor ads, and for video or static also mines what is
  getting organic reach on TikTok, Instagram, YouTube Shorts and X. Every reference is labelled
  paid or organic, so boosted "winners" are not copied. Outputs a ranked angle bank; for video
  and static, each angle comes with a creative (a format or template to make it with) and can
  be handed straight to making the ads.
tags: [ads]
---

# Ad Angle Miner

An ad is **copy plus creative**. This skill finds the angles real buyers respond to and returns
the part the user asked for:

- **Copy**: the angle bank: pain, outcome and proof language with headlines and body copy.
- **Video**: ranked video ad ideas: each angle as a hook, a video format and the posts or ads behind it.
- **Static**: ranked static ad ideas: each angle as a headline plus a static template to make it with.

**Core principle:** The best ad angles aren't invented in a brainstorm. They're extracted from what
real people are already saying and watching. This skill finds those angles and ranks them by the
strength of the evidence, and a paid ad's reach never counts as evidence that it works.

**Do only the work the chosen output needs.** A copy request never searches for videos, reads ad
formats or looks at templates. A video request doesn't mine G2 reviews unless the user asks.

## When to Use

- "What angles should we run in our ads?" / "Mine reviews for ad messaging" (copy)
- "What are people complaining about with [competitor]?" (copy)
- "What video ads should I make?" / "Give me video ad ideas / hooks for [brand]" (video)
- "What's working in my competitors' video ads?" (video)
- "Give me static ad ideas" / "What image ads should we run?" (static)
- "I need fresh ad angles, not the same tired stuff" (ask which output, below)

## Prerequisites

- **GooseWorks MCP connector** (recommended): brand context, competitors, the competitor ad
  library, saved inspiration, ScrapeCreators searches through the GooseWorks proxy (no key), the
  video format catalogue and the static template library. With it connected, no API key is needed.
- **Without GooseWorks**: a direct ScrapeCreators key for social and ad-library evidence
  (`scrapecreators-api`, `competitor-ad-intelligence`) and web search. Video and static outputs
  then recommend a format in words instead of a catalogue entry, and there is no hand-off step.
- **Optional:** `APIFY_API_TOKEN` for bulk Amazon review and Reddit scraping (copy mode). Without it,
  use web search for reviews and `scrapecreators-api` for Reddit.

## Phase 0: Intake

### 0A: Choose the output (this decides everything else)

Read it from the request:

| The user says | Output |
|---|---|
| angles, messaging, copy, headlines, pain points, "what should we say" | **copy** |
| video, UGC, reels, TikTok, "what video should I make" | **video** |
| static, image ads, banners, "what image ads" | **static** |
| "ads" with no hint, or "fresh angles" | **ask once**: "Copy angles only, video ad ideas, or static ad ideas?" |

The user can pick more than one (for example video and static); then run the union of their
sources, once. Never widen the scope on your own.

### 0B: What each output runs

| Source | copy | video | static |
|---|---|---|---|
| Brand context (1-0) | yes | yes | yes |
| Reviews: G2 / Capterra / Amazon (1A) | yes | only if asked | only if asked |
| Reddit / community (1B) | yes | only if asked | only if asked |
| Social comments (1C) | yes | yes (on the top posts found in 1F) | no |
| Competitor ads (1D) | copy text only | video ads first, then any | static ads first, then any |
| Internal data (1E) | if provided | if provided | if provided |
| Organic short-form reach (1F) | **no** | yes | no |
| Format catalogue / template library (Phase 2.5) | **no** | video formats | static templates |

### 0C: The rest of the intake

Take what the request and the brand context already answer; ask only for what's missing, in one message:

1. **Your product**: name and what it does in one sentence (with GooseWorks: the brand context).
2. **Competitors**: 2-5 names (with GooseWorks: the brand's tracked competitors, or the suggested ones).
3. **ICP**: who you're targeting.
4. **Search terms** the user wants covered, if any. Use them first.
5. **Angles already tested**, so we can skip them.

Never invent a customer, a result or a claim: an angle may only promise what the brand's own facts support.

## Phase 1: Source Collection

Run only the sections Phase 0B selected.

### 1-0: Brand Context (GooseWorks)

With the GooseWorks MCP: resolve the brand with `brand_read` (one brand in the org means use it
and say which), then read it with sections summary, products, competitors, accounts and learnings.
Note what it sells (physical product, app, service), who buys it, its claims and can't-say rules,
which products have clean photos, and whether it has screen recordings or footage. `competitor_read`
lists tracked competitors; with none, its suggestions view names likely ones. Say them in one line
and carry on.

### 1A: Review Mining (Apify)

Use the Apify Amazon Reviews Scraper (or web_search for G2/Capterra/TrustRadius reviews).

**Option 1: Amazon product reviews via Apify**

Start a run of the `web_wanderer/amazon-reviews-extractor` actor:

```
POST https://api.apify.com/v2/acts/web_wanderer~amazon-reviews-extractor/runs?token=$APIFY_API_TOKEN
Content-Type: application/json

{
  "products": [
    "https://www.amazon.com/dp/PRODUCT_ASIN"
  ],
  "maxReviews": 100
}
```

Poll until the run finishes:

```
GET https://api.apify.com/v2/acts/web_wanderer~amazon-reviews-extractor/runs/{RUN_ID}?token=$APIFY_API_TOKEN
```

When `status` is `SUCCEEDED`, fetch results:

```
GET https://api.apify.com/v2/datasets/{DATASET_ID}/items?token=$APIFY_API_TOKEN
```

**Output fields:** Each review has `rating` (1-5), `reviewTitle`, `reviewText`, `reviewDate`, `verifiedPurchase` (bool), `productAsin`, `productTitle`, `helpfulVoteCount`.

**Option 2: G2/Capterra/TrustRadius reviews via web_search**

For B2B products, run web searches to find review content:

```
web_search: "<product_name> reviews site:g2.com"
web_search: "<product_name> reviews site:capterra.com"
web_search: "<product_name> reviews site:trustradius.com"
web_search: "<competitor_name> reviews site:g2.com"
```

Focus on:
- **1-2 star reviews of competitors** — Pain they're failing to solve
- **4-5 star reviews of you** — Outcomes that delight buyers
- **4-5 star reviews of competitors** — Strengths you need to counter or match
- **Review language patterns** — Exact phrases buyers use

### 1B: Reddit/Community Mining (Apify)

Use the `trudax/reddit-scraper-lite` actor to search Reddit for relevant threads:

**Search by keyword:**
```
POST https://api.apify.com/v2/acts/trudax~reddit-scraper-lite/runs?token=$APIFY_API_TOKEN
Content-Type: application/json

{
  "searches": [
    "<product category> OR <competitor> OR <pain keyword>"
  ],
  "maxItems": 50
}
```

**Browse a specific subreddit:**
```
POST https://api.apify.com/v2/acts/trudax~reddit-scraper-lite/runs?token=$APIFY_API_TOKEN
Content-Type: application/json

{
  "startUrls": [
    {"url": "https://www.reddit.com/r/SUBREDDIT_NAME/hot/"}
  ],
  "maxItems": 50
}
```

Poll until complete:

```
GET https://api.apify.com/v2/acts/trudax~reddit-scraper-lite/runs/{RUN_ID}?token=$APIFY_API_TOKEN
```

Fetch results when `status` is `SUCCEEDED`:

```
GET https://api.apify.com/v2/datasets/{DATASET_ID}/items?token=$APIFY_API_TOKEN
```

**Output fields:** Each item has `dataType` ("post" or "comment"), `title` (posts only), `body`, `communityName`, `upVotes`, `numberOfComments` (posts), `url`, `createdAt`.

Extract:
- Questions people ask before buying
- Complaints about current solutions
- "I wish [product] would..." statements
- Comparison threads (vs discussions)

### 1C: Social Post and Comment Mining

Use `scrapecreators-api` to collect relevant X posts plus Instagram, TikTok, YouTube, or Facebook posts where the audience is discussing the problem. Run `comment-mining` on the highest-signal threads. Use web search only as a fallback:

```
web_search: "<competitor> (frustrating OR broken OR hate) site:x.com"
web_search: "<competitor> (love OR switched to OR replaced) site:x.com"
web_search: "<product category> (recommendation OR alternative OR looking for) site:twitter.com"
web_search: "<competitor> site:x.com" (for general sentiment)
```

Run 3-5 queries covering:
- Competitor complaints and frustrations
- Product category praise / switching stories
- "What do you use for X?" buying-intent threads

### 1D: Competitor Ad Mining

With the GooseWorks MCP, read the competitor ads already imported: `ads_template_read` in
competitor mode (filter by format video or image to match the output; page with a small limit,
rows are heavy). If a tracked competitor has none, `ads_library_scrape` with its source id starts
a free import; read it when the job finishes. For each ad keep the Ad Library link
(facebook.com/ads/library with the ad's source ad id), the hook (first line of the primary text),
the offer, the CTA and **days running** (end date minus start date). The library may hold only
image ads for a competitor; their copy is still angle evidence.

Otherwise:

Use `competitor-ad-intelligence` for structured Meta and Google ad-library collection. Use web search only to verify an advertiser or fill a documented gap:

```
web_search: "<competitor_name> site:facebook.com/ads/library"
web_search: "<competitor_name> facebook ads library"
web_search: "<competitor_name> ad creative examples"
```

This reveals:
- Angles they've validated (long-running ads = working)
- Angles they're testing (new ads)
- Angles nobody is running (white space)

### 1F: Organic Short-Form Reach (video only)

What is getting **earned** reach right now, on TikTok, Instagram Reels, YouTube Shorts and X.

1. **Free first** (GooseWorks): the competitor dossiers (`competitor_read` with a slug) hold recent
   posts; `social_inspiration_library` and `social_inspiration_search` hold saved and researched posts.
2. **Paid searches, ask once**: "I'd run about N searches (TikTok, Instagram Reels, YouTube Shorts,
   plus X mentions of your competitors). Each is billed per call. Go ahead?" On yes, run these
   through `scrapecreators-api` (GooseWorks: `data_call_provider` with provider scrapecreators,
   GET). Terms: the user's own first, then the category, the main problem it solves, each
   competitor's name.

   | Platform | Path | Query | Notes |
   |---|---|---|---|
   | TikTok | /v1/tiktok/search/keyword | query, date_posted last-3-months, sort_by most-liked | Views in statistics.play_count |
   | Instagram Reels | /v2/instagram/reels/search | query, date_posted last-month | Google-indexed, so best-effort; page 1-11 |
   | YouTube Shorts | /v1/youtube/search | query, type shorts, **nothing else** | Adding uploadDate or sortBy with type shorts returns no results. Rows carry only id, url, title and views: for the 3-5 you'd cite, call /v1/youtube/video (url) for the channel and publish date |
   | X | none | none | ScrapeCreators has no X keyword search. Use `competitor_search_mentions` with platform x, or /v1/twitter/user-tweets for a competitor's own handle |

   The full, current list is the official OpenAPI (docs.scrapecreators.com/openapi.json). If a
   call errors, read it there; never guess a path. On no: continue with the free evidence and
   say the list has no fresh social data behind it.
3. Keep vertical videos only. Keep posts far above their account's usual views (an outlier at 10×
   its normal beats a big account's average post), from the last ~90 days.
4. Watch the 3-5 strongest (the `watch` skill, or `social_inspiration_watch` for saved posts) so the
   hook and structure you describe are what's actually in them.

### 1G: Label Every Reference Paid or Organic (video and static)

For every post or ad you might cite:

- **paid**: it came from an ad library; or the post is marked as an ad (TikTok ad / commercial
  content flag, Instagram paid partnership or sponsor tags, "Sponsored", a YouTube paid-promotion
  disclosure: /v1/youtube/video/sponsors returns isPaidPromotion and the likely sponsor); or the
  same creative also runs as that advertiser's ad (**boosted**).
- **organic**: a post with none of the above.
- **unknown**: you can't tell. Say so; never guess organic.

Organic strength = reach relative to the account's normal, and recency. Paid strength = how long
the advertiser kept running it and how many variants they made. **Never treat a paid post's views
as proof.** Money bought them.

### 1E: Internal Data (Optional)

If the user provides support tickets, NPS comments, or sales call transcripts — ingest and tag with the same framework below.

## Phase 2: Angle Extraction

Process all collected data through this extraction framework:

### Angle Categories

| Category | What to Look For | Ad Power |
|----------|-----------------|----------|
| **Pain angles** | Specific frustrations with status quo or competitors | High — pain motivates action |
| **Outcome angles** | Desired results buyers describe in their own words | High — positive aspiration |
| **Identity angles** | How buyers describe themselves or want to be seen | Medium — emotional resonance |
| **Fear angles** | Risks of NOT switching or acting | Medium — loss aversion |
| **Competitive displacement** | Specific reasons people switched from a competitor | Very high — direct comparison |
| **Social proof angles** | Outcomes or metrics buyers cite in reviews | High — credibility |
| **Contrast angles** | Before/after or old way/new way framings | High — clear value prop |

### For Each Angle, Extract:

1. **The angle** — One-sentence framing
2. **Proof quotes** — 2-5 verbatim quotes from sources
3. **Source count** — How many independent sources mention this?
4. **Competitor weakness?** — Does this exploit a specific competitor's gap?
5. **Emotional register** — Frustration / Aspiration / Fear / Relief / Pride
6. **Recommended format** — Search ad / Meta static / Meta video / LinkedIn / Twitter

## Phase 2.5: Match Each Angle to a Creative (video and static only)

Skip this phase for copy.

**Video.** Read the format catalogue (GooseWorks: `video_catalog_list` with kind formats and the
brand id). Each row has a template id, a card description, best-for, needs and demo examples. Only
these formats can be made; never map to one that isn't listed. For each angle write:

- **Hook**: the first line or shot, in the brand's voice, 12 words or fewer.
- **Format**: the template id, its card description quoted (never reworded), and why it fits.
  Match the product to the format: a creator holding a product needs a physical product; a
  screen-recording format needs an app.
- **Needs**: the card's needs against what the brand has. A missing need lowers the rank; say it.
- **References**: 1-3 links, each with platform, account, paid / organic / unknown, and the number
  that matters (views vs usual, or days running).

**Static.** Find a template for each angle (GooseWorks: `ads_template_read` in static_library mode
filtered by industry or style, community mode, or a competitor ad from 1D as a remix source). For
each angle write the headline, the template id with one line on why its layout fits, what the
brand must supply (product photo, logo), and the references.

Adapt the pattern, never copy: take the hook shape, structure and angle, never another brand's
words, claims, faces, footage or offer. Spread the list: no more than 3 ideas on one format or
template, and no more than 3 on one angle.

## Phase 3: Scoring & Ranking

Score each angle on:

| Factor | Weight | Description |
|--------|--------|-------------|
| **Evidence strength** | 30% | Number of independent sources mentioning it |
| **Emotional intensity** | 25% | How strongly people feel about this (language intensity) |
| **Competitive differentiation** | 20% | Does this set you apart, or could any competitor claim it? |
| **ICP relevance** | 15% | How closely does this match the target buyer's world? |
| **Freshness** | 10% | Is this angle already overused in competitor ads? |

**Total score out of 100. Rank all angles.**

For video and static, evidence strength counts organic outliers and long-running ads most, and an
angle backed **only by paid references caps at 60**. Add readiness: an idea whose format or
template needs something the brand lacks drops a tier.

## Phase 4: Output Format

```markdown
# Ad Angle Bank — [Product Name] — [DATE]

Sources mined: [list]
Total angles extracted: [N]
Top-tier angles (score 70+): [N]

---

## Tier 1: Highest-Conviction Angles (Score 70+)

### Angle 1: [One-sentence angle]
- **Category:** [Pain / Outcome / Identity / Fear / Displacement / Proof / Contrast]
- **Score:** [X/100]
- **Emotional register:** [Frustration / Aspiration / etc.]
- **Proof quotes:**
  > "[Verbatim quote 1]" — [Source: G2 review / Reddit / etc.]
  > "[Verbatim quote 2]" — [Source]
  > "[Verbatim quote 3]" — [Source]
- **Source count:** [N] independent mentions
- **Competitor weakness exploited:** [Competitor name + specific gap, or "N/A"]
- **Recommended formats:** [Search ad headline / Meta static / Video hook / etc.]
- **Sample headline:** "[Draft headline using this angle]"
- **Sample body copy:** "[Draft 1-2 sentence body]"

### Angle 2: ...

---

## Tier 2: Worth Testing (Score 50-69)

[Same format, briefer]

---

## Tier 3: Emerging / Low-Evidence (Score < 50)

[Brief list — angles with potential but insufficient evidence]

---

## Competitive Angle Map

| Angle | Your Product | [Comp A] | [Comp B] | [Comp C] |
|-------|-------------|----------|----------|----------|
| [Angle 1] | Can claim ✓ | Weak here ✗ | Also claims | Not relevant |
| [Angle 2] | Strong ✓ | Strong | Weak ✗ | Not relevant |
...

---

## Recommended Test Plan

### Week 1-2: Test Tier 1 Angles
- [Angle] → [Format] → [Platform]
- [Angle] → [Format] → [Platform]

### Week 3-4: Test Tier 2 Angles
- [Angle] → [Format] → [Platform]
```

Save to `angle-bank-[YYYY-MM-DD].md` in the current working directory (or user-specified path).

**Copy** returns exactly the bank above. **Video and static** return the same bank trimmed to one
line per angle, plus the idea table below.

### Video / Static Ideas (video and static only)

Print one table in the chat, best first, with 10-15 ideas. Every idea needs at least one link the
tools actually returned; no link, no idea.

| # | Idea (hook or headline) | Angle | Format / template | Why it should work | References | Needs | Score |
|---|---|---|---|---|---|---|---|
| 1 | "I stopped taking melatonin. Here's why." | Contrast | Myth vs fact ([demo](https://…)) | Organic: 3 creators at 8-15× their usual views in 60 days | [TikTok @a](https://…) (organic, 12× usual) · [Meta ad](https://…) (paid, 94 days) | product photo ✓ | 86 |

Under it, one line: which sources ran and which were skipped (and why), and how many references
were paid vs organic. Also save the ideas as JSON next to the angle bank (rank, hook, angle,
format or template id, why, references with url / platform / account / reach / metric, needs,
score) so a later session can pick them up without re-running research.

Then ask: "Which ones should I make? Pick up to 5, or say 'the top 3'."

## Phase 5: Make These (video and static, GooseWorks only)

For the picked ideas, with no manual step in between:

- **Video**: one project per idea with `video_project_upsert` (brand id, a short name from the
  hook, format set to the template id, and no brief, since a brief makes a multi-concept batch).
  Then make them one after another with the `goose-video-local` skill; each idea (hook, angle,
  audience, must-say and avoid notes, references as style guidance) is that project's brief, so
  it never re-asks. It needs a terminal agent (Claude Code, Codex, Cursor); on a hosted connector,
  say the ideas are ready and making them needs one of those.
- **Static**: hand the picked template ids and each idea's headline and angle to the `goose-ads`
  skill as the source templates and the brief.

Up to 5 per request; offer the rest after. Each paid step of making an ad is approved before it runs.

## Tools Required

- **GooseWorks MCP** (recommended): `brand_read`, `competitor_read`, `competitor_search_mentions`,
  `ads_template_read`, `ads_library_scrape`, `social_inspiration_library`, `social_inspiration_search`,
  `data_call_provider`, `video_catalog_list`, `video_project_upsert`
- **Optional environment variable:** `APIFY_API_TOKEN` — for Apify actors (review scraper, Reddit scraper), copy mode
- **`comment-mining`** — customer language from social and ad comment threads
- **`competitor-ad-intelligence`** — structured ad-library research through ScrapeCreators
- **Web search** — built into your AI agent for verification and review sources

## Trigger Phrases

- "Mine ad angles from reviews"
- "What angles should we run?"
- "Find pain language for our ads"
- "Build an ad angle bank for [client]"
- "What are people complaining about with [competitor]?"
- "What video ads should I make for [brand]?"
- "Give me static ad ideas for [brand]"
