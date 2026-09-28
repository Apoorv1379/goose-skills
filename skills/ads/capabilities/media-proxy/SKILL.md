---
name: media-proxy
description: Shared helper that routes ALL paid media generation (FAL image/video, ElevenLabs music) through the GooseWorks proxies so every call bills the Ads agent — never a provider SDK's default host. Host-swaps the FAL queue URLs, loads the agent token from the sandbox env (GW_MEDIA_PROXY_TOKEN) or ~/.gooseworks/credentials.json, and returns the result CDN URL. Every video-ad media capability imports this; templates never call a provider directly.
status: active
---

# media-proxy

The foundation capability for paid media in the video-ad pipeline. It fixes the
auth-path conflict where engine scripts called FAL/ElevenLabs **directly** (billing
the wrong account): all paid calls now go through
`<api_base>/api/internal/{fal-proxy,elevenlabs-proxy}` with `?token=&agent_id=`, which
**bills the Ads agent**.

## Crash-resume (never lose / double-bill a paid render)

A FAL submit BILLS immediately, but the local backend can blip during a multi-minute
render. Two built-in protections (automatic for every capability that imports this):
- **Poll-through-outage** — `_fal_run`'s poll loop re-attaches to the same status/result
  URL through `connection refused` / timeout blips instead of crashing.
- **Persist + resume** — each submit's `request_id` + poll URLs are written to
  `~/.gooseworks/pending-fal-jobs/`. If the poller still dies, **re-attach instead of
  re-firing** (re-firing double-bills): `resume_fal(request_id)` in Python, or the CLI:

  ```bash
  resume.py --list                              # resumable (submitted, unfinished) jobs
  resume.py --request-id <id> --out final.mp4   # poll to completion + download
  ```
  `resume_fal` NEVER re-submits, so it can't double-charge.
- **Poll timeout never resubmits (GOOSE-3729)** — polling gives up after
  `default_poll_timeout(model)`: **1800s for video / lipsync / audio-driven models**,
  600s for images (`GW_FAL_POLL_TIMEOUT_S` overrides; `timeout_s=` per call). A timeout
  raises `FalPollTimeout` carrying `.request_id` + `.model_path` — the job is still
  running and already paid for. **Re-attach with `resume_fal(e.request_id)`; never call
  `fal_generate*` again for it** (a veed/fabric lipsync once finished 26s after a 600s
  poller quit, and the retry paid for a second identical job).
- **Proxy dedupe back-stop** — the fal proxy returns the already-running job for an
  identical submit (same agent + model + body) within 30 min, so an accidental
  resubmit re-attaches instead of paying twice (response header `x-gw-deduped: 1`).
  For a **deliberate re-roll** of the same input (want a new take), pass
  `new_take=True` (`fal_generate(..., new_take=True)` / `fal_generate_video(...)`),
  which sends `x-gw-no-dedupe: 1` (raw HTTP callers: that header or `?dedupe=0`).

## Use it

```python
from media_proxy import fal_generate, fal_generate_video, eleven_music, download

# image (nano-banana / gpt-image / etc.) — inputs must be PUBLIC urls
img = fal_generate("fal-ai/nano-banana/edit",
                   {"prompt": p, "image_urls": [product_url], "aspect_ratio": "9:16"})
# video i2v (kling / seedance / veo)
vid = fal_generate_video("fal-ai/kling-video/v2.1/standard/image-to-video",
                         {"prompt": p, "image_url": keyframe_url, "duration": "10"})
# music bed
eleven_music(prompt, 10500, "music.mp3", force_instrumental=True)
```

## Save-as-you-go digest (GOOSE-3731)

`input_digest(model, args)` = sha256 of the canonical JSON `{"model", "args"}` (sorted
keys, no whitespace), first 32 hex chars. Pass it with the MCP `media_upload` of the
result (plus an `ingredient_key` such as `vo/scene-03`); on a resume, `media_list
{ ingredient_key }` returns the saved file and its digest, and the file is reused only
when the digest of the args you would send now is the same.

```python
from media_proxy import input_digest, eleven_tts
args = {"text": line, "voice_id": vid, "model_id": "eleven_v3"}
digest = input_digest("elevenlabs/tts", args)
```

Hash only what determines the output. Swap any expiring input URL (presigned / proxy)
for that input's own ingredient_key + digest first, or the digest never matches.

## Contracts (load-bearing)

- **Bills the Ads agent** — `?token=&agent_id=` from `~/.gooseworks/credentials.json`
  (the CLI writes it; run `gooseworks login` if missing). In a GooseWorks cloud sandbox
  (coworker chat) the env wins instead: `GW_MEDIA_PROXY_TOKEN` (a per-session token that
  already binds agent/org/user) + `GW_API_BASE`; `GW_PROJECT_ID` attributes spend.
- **Host-swap the FAL queue URLs** — submit returns `status_url`/`response_url` on
  `queue.fal.run`; the helper rewrites them to the proxy base (keeps the path). Never
  poll `queue.fal.run` directly (401 + burns credits).
- **FAL inputs that are local files must be PUBLIC urls.** The orchestrator hosts a
  local image/audio via the MCP `get_upload_url` → `get_download_url` presigned URL and
  passes THAT url in. This module does not do MCP uploads (prefer the presigned url;
  `fal-storage-proxy` may 404).
- **Only the final `*.fal.media` url is a real public URL** — everything else is behind
  the proxy.

## Related
- Used by `create-image-fal`, `create-video-fal`, `create-music-elevenlabs`.
- The `goose-video` orchestrator hosts local inputs (MCP upload → presign) before calling these.
