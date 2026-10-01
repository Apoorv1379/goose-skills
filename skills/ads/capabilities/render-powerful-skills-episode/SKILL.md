---
name: render-powerful-skills-episode
description: Render one episode of the "Powerful Claude Skills You Should Know" format — a locked presenter delivers a punchy hook to camera, a screen recording zooms through the Gooseworks skills site into a real terminal running the featured skill, the file that run produced is shown as the payoff, and the presenter closes. The character, the voice, the room, the camera choreography and the audio levels are all fixed; only the featured skill, the five spoken lines and the payoff spec change per episode. Captures the screen recording locally (Playwright, free), generates the two character clips (Veo) and the voiceover (a locked MiniMax clone), screens every take on voice, music bed, dialogue and how it ends, then assembles to a 1080x1920 master and asserts twelve gates on the finished file. Use for the powerful-skills format.
status: active
---

# render-powerful-skills-episode

The renderer for the **powerful-skills** format. One episode features one skill from
the Gooseworks library; everything else about the film is fixed, which is the point —
the series reads as one person recording in one room on different days.

## What it makes

Four sections, 22-34s, 1080x1920 at 30fps:

| section | content | paid |
|---|---|---|
| hook | the presenter, one line to camera, captioned `Powerful Claude Skills You Should Know! Pt. N` | Veo |
| terminal | the skills site, the install command, then the featured skill running in a real terminal | no, local capture |
| results | the file the run produced | no, rendered locally |
| closer | the presenter, the send line | Veo |

## What changes per episode

Four things, and exactly one of them is paid:

| | how | paid |
|---|---|---|
| the featured skill | picked off the live site, or named | no |
| the screen recording | the site's own demo, captured locally | no |
| the five spoken lines | a locked voice clone | cents |
| **the hook and closer clips** | **Veo, because the character speaks new words** | **$7.20** |

## Run it

```bash
# free: pick the skill, read its demo, write the lines, capture the recording,
# draft the payoff spec, print the itemised bill, stop
python scripts/new_episode.py --series <series>/

# check the demo is long enough to carry the voice BEFORE paying for anything
python scripts/check-demo-length.py <ep>/screen-recording/zoom-edit.mp4 "candidate"

# write <ep>/working/{board,sheet,report}-spec.json from the run's real output,
# render the still, look at it, then:
python scripts/make_episode.py --episode <ep>/ --yes
```

The second command runs unattended to a verified master: both Veo waves together, eight
takes screened in parallel, a take failing only on a baked-in music bed rescued by source
separation, the pair picked on how it ends, trimmed on its last spoken word, the voiceover
re-rolled until it matches the locked voice on both pitch and timbre, the payoff built, the
edit re-timed to the voice, the SFX and caption laid in, and twelve gates asserted.

## What it needs

- `FAL_KEY` — Veo, the MiniMax clone and speech-to-text all go through fal.
- `ffmpeg`, `ffprobe`, `node` with Playwright, and Python with numpy and Pillow.
- A series folder holding `locked/` (this atom ships a working one) and an `episode.json`
  per episode.

## The payoff is the one human decision

What the run WROTE picks the treatment, and the treatment is approved from a still before
anything is assembled:

| the run wrote | spec | script |
|---|---|---|
| a report or summary | `working/report-spec.json` | `make-report-broll.py` |
| a file of rows | `working/sheet-spec.json` | `make-sheet-broll.py` |
| a finding about people or organisations | `working/board-spec.json` | `make-board-broll.py` |

Every value in that spec comes from the demo's own transcript. Inventing a figure or a name
the run did not produce is the same defect as using stills from a different run.

## Twelve gates on the finished file

Reference image, handheld shake on both clips, voice pitch, voice spectrum, brightness, dead
air before the closer, caption position, caption text, dialogue, the notification cue landing
on the deliverable, loudness and duration. **Count the lines: twelve.** Three of them skip
silently when an input is missing, and a run that reports nine as "all checks passed" has
told you nothing.

## What the gates cannot see

A blink on the last frame, a terminal that has stopped printing, and a whoosh lagging its
cut. All three have shipped past a clean report. Watch the finished cut and open
`review/end-hook.png` and `review/end-closer.png`.

## Cost

$7.23 - $7.51 for one finished episode: $7.20 of Veo (eight takes at $0.90, four seeds for
each shot in one wave) plus the voice clone and the speech-to-text calls. A shot where
nothing passes needs another $3.60; that has not happened in the last nine recorded
episodes.
