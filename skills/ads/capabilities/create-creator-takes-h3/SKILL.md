---
name: create-creator-takes-h3
description: Generate an AI creator talking to camera, saying an approved script, as one continuous track — H3 Max reference-to-video through the GooseWorks fal-proxy (bills the Ads agent). Plans takes on line boundaries under H3's 15s cap, dry-runs a cost estimate, generates the first take alone so its voice can be locked and passed to every later take, joins takes with measured 0.10s dissolves, and moves each line's timing onto the words actually spoken. Use for any format with a generated creator speaking a script (split-screen, screen inserts, talking-head ads).
status: active
---

# create-creator-takes-h3

A generated creator who says an exact script, with one consistent face, room and voice
across several takes.

| Script | What | Cost |
|---|---|---|
| `plan_takes.py` | split lines into takes (under 15s each) and write the prompts | free |
| `run_takes.py` | generate the takes, dry run unless `--go` | **paid** (H3 Max, ~$0.16/s at 1080P) |
| `join_takes.py` | join into one creator track | free |
| `align_beats.py` | move line timings onto the spoken words | free |

`character-prompt.json` is a realism prompt for making the character still (use it with
`create-image-fal`, e.g. `fal-ai/nano-banana-2`): fill its slots, generate 2–4 options, let the
user pick one.

## Run

```bash
python plan_takes.py --beats cutlist.json --character character.json --out work/takes
python run_takes.py --spec work/takes/takes.json                  # dry run: plan + estimate
python run_takes.py --spec work/takes/takes.json --only t1 --go   # after the user approves
python run_takes.py --spec work/takes/takes.json --go             # the rest, voice chained
python join_takes.py --spec work/takes/takes.json --end <reel length> --out work/creator.mp4
# caption-burn: transcribe.py --media work/creator.mp4 --out work/creator.words.json
python align_beats.py --beats cutlist.json --words work/creator.words.json \
    --out cutlist.aligned.json --max-end <creator.mp4 length>
```

`character.json`:

```json
{"image": "character.png",
 "identity": "a man in his late 20s, South Asian, dark curly hair, charcoal t-shirt",
 "environment": "a warm plain room, a framed print behind him, window daylight",
 "delivery": "optional: how they speak"}
```

`identity` and `environment` go into every take **word for word**. Write them once from the
approved still, then never retype them: a person described two ways drifts between takes.

## Rules

1. **Lock order: script, then character still, then takes.** Show the still before any
   take. A take costs dollars; a still costs cents.
2. **First take alone, then listen.** Every later take copies t1's voice (its audio is
   passed as `reference_audio_urls`). A robotic or mismatched voice in t1 is in every
   take. If the user rejects it, `--reseed t1` and generate t1 again.
3. **Voice matches the person.** Age, gender and accent follow the character. The
   prompt's delivery line sets the tone; change it through `delivery`, not by editing
   the prompt file.
4. **No square brackets in lines.** H3 speaks them aloud; `plan_takes.py` refuses them.
5. **Dialogue stays verbatim.** Takes are sent with `prompt_expansion_mode: disabled`.
6. **Takes split between lines, never inside one**, and run 0.6s past the last word.
7. **Join with a 0.10s dissolve, never 0.20s.** At 0.20s both poses show through the blend
   ("two pairs of hands"). Measured: 11.04 peak change at 0.10s vs 13.30 for a hard cut.
8. **One filter graph.** Joining files with the concat demuxer puts black frames at every
   boundary; `xfade` with a late offset silently concatenates instead of overlapping.
9. **Watch every take end to end before joining**: eyes on the lens to the last word,
   hands, identity, voice. A wrong still ("anchor") makes every take wrong the same way;
   if all takes fail alike, fix the still, not the seed.
10. **Mannerism clips are optional and need rights.** `--mannerism` passes a muted
    motion-reference clip. Its gaze transfers, so check the eyeline on every frame. Never
    use another brand's creator footage.
11. **Seeds are pinned.** Re-running an unchanged take repays for the same clip. Existing
    take files are skipped.
