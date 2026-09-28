---
name: caption-burn
description: Word-timed burned-in captions for a finished vertical video. transcribe.py gets word timings from the video's own audio through the GooseWorks proxy (fal Whisper, bills the Ads agent, cents); captions.py burns one to three words at a time with Pillow + ffmpeg (no libass needed), either pinned to a split-screen seam (plate 25% above / 75% below) or at a fixed height, in a plate or outline style, holding the last caption (the CTA) to the final frame. Use as the last step of any talking video ad.
status: active
---

# caption-burn

Captions timed to what is actually said in the finished video, not to the script's
estimate.

## Run

```bash
python transcribe.py --media reel.mp4 --out reel.words.json          # paid, cents
python captions.py --video reel.mp4 --beats cutlist.aligned.json \
    --words reel.words.json --out final.mp4 [--style plate|outline] [--highlight Brand]
```

`--beats` supplies the lines (`vo`), their timing and, for split layouts, `seam`, `size`
and each beat's `state`. Any file with `beats: [{start, end, vo}]` works.

## Placement and style

- `--anchor seam` (the default when the beats have a seam): on `split` beats the plate is
  pinned to the seam, **positioned by the plate, not the text**: 25% of the plate above the
  line, 75% below. Full-frame beats use `--full-y`.
- `--anchor fixed --y 0.62`: every caption's plate centred at that fraction of the height.
- `plate` (default): white bold on a dark grey rounded plate, 1–2 words, cap ~0.019 H.
- `outline`: white bold with a dark outline, no plate, 1–3 words, cap ~0.034 H.
- `--highlight WORD` colours that word yellow (the CTA keyword). Repeatable.

## Rules

1. **Transcribe the FINISHED audio.** Joining takes and aligning lines shifts timing;
   only the final video's audio gives correct cues.
2. **The last caption holds to the last frame.** It is the call to action.
3. **A word Whisper writes differently** ("200" for "two hundred") is interpolated between
   its neighbours rather than dropped or stretched over the whole line.
4. **Without `--words` timing is estimated** from syllables. Use that to judge placement,
   never to ship.
5. **Fonts:** a bold sans is found on macOS, Linux or Windows; if none is present, Roboto
   Bold is fetched once into `~/.cache/gooseworks/fonts`. `--font` or `GW_CAPTION_FONT`
   overrides.
