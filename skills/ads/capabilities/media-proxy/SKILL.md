---
name: media-proxy
description: Shared helper that routes ALL paid media generation (FAL image/video, ElevenLabs music) through the GooseWorks proxies so every call bills the Ads agent — never a provider SDK's default host. Host-swaps the FAL queue URLs, loads the agent token from ~/.gooseworks/credentials.json, and returns the result CDN URL. Every video-ad media capability imports this; templates never call a provider directly.
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

## Contracts (load-bearing)

- **Bills the Ads agent** — `?token=&agent_id=` from `~/.gooseworks/credentials.json`
  (the CLI writes it; run `gooseworks login` if missing).
- **Host-swap the FAL queue URLs** — submit returns `status_url`/`response_url` on
  `queue.fal.run`; the helper rewrites them to the proxy base (keeps the path). Never
  poll `queue.fal.run` directly (401 + burns credits).
- **FAL inputs that are local files must be PUBLIC urls.** The orchestrator hosts a
  local image/audio via the MCP `get_upload_url` → `get_download_url` presigned URL and
  passes THAT url in, or call `fal_upload(path)`, which puts the file on the fal CDN
  through the fal-storage-proxy (free, verified 2026-09-28) and returns its public url.
- **Only the final `*.fal.media` url is a real public URL** — everything else is behind
  the proxy.

## No CLI login? The MCP relay

A session that only has the GooseWorks MCP connector (the Claude desktop app, a Codex
session without `gooseworks login`) has no `~/.gooseworks/credentials.json`, so scripts
cannot reach the proxies over HTTP. Then every paid call is **relayed through the agent**:

1. The script writes the exact MCP tool call to `working/mcp-requests/<kind>-<hash>.json`
   and exits with code **3**, printing what to do.
2. The agent makes that call through the connector:
   - fal → `data_post_provider { provider: "fal", path: <model>, body, project_id }`, then
     `job_get { job_id }` until `complete`; save `result.output` (fal's JSON).
   - ElevenLabs → `data_post_provider { provider: "elevenlabs", ... }`; save the reply
     (it carries `download_url`).
   - A local file → `media_upload { scope: "video_project", ... }` with the bytes of
     `local_file`; save `{ "url": <its url> }`.
3. It saves that JSON to `save_result_to` and **re-runs the same command**. The script finds
   the result and continues; the next paid call relays the same way.

Set `GW_PROJECT_ID` (required: every call is billed to that video project, the same
attribution the HTTP proxy records) and `GW_BRAND_ID` (for uploads). The MCP tools bill
through the same server proxy code, so price and project attribution are identical.
`GW_MEDIA_VIA=mcp` forces the relay (e.g. the CLI login points at another environment);
`GW_MEDIA_VIA=proxy` forces HTTP.

## Related
- Used by `create-image-fal`, `create-video-fal`, `create-music-elevenlabs`.
- The `goose-video` orchestrator hosts local inputs (MCP upload → presign) before calling these.
