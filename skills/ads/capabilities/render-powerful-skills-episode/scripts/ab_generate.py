#!/usr/bin/env python3
"""Generate MiniMax H3 character takes at N seeds from the locked character + voice refs.

The generation half of the A/B harness. Measure the results with ab_report.py.

Why this exists: the prompt cannot pin the character's voice (critical knowledge #14),
but H3 accepts an audio reference and transfers the voice from it. This script sends the
SAME locked image and the SAME locked voice reference on every call, varying only the
seed, so the question "does voice transfer actually hold across seeds?" gets a clean
answer before ten episodes are committed to it.

References are cited positionally in the prompt. Images become "Image 1", "Image 2", ...
in the order passed; the voice reference becomes "Audio 1". The prompt must name them --
the model does not guess which reference does what. A default prompt preamble is prepended
unless --no-preamble is passed; it carries the format's fixed requirements: an explicit
character-preservation list, the monitor-content restriction, the selfie framing with
caption headroom, say-the-line-once, and a trailing Sound: clause that closes every
musical path (see the PICTURE / SOUND constants below for why that shape).

Constraints enforced before spending:
  - duration 5-15s
  - at most 12 reference files total
  - audio references cannot be sent alone; at least one image or video must accompany them

Costs money. Prints the plan and requires --yes to submit.

Usage:
    ab_generate.py --image locked/character-selfie.png --voice-ref locked/voice-ref.wav \\
        --line "This skill literally turns Claude into your own SEO auditor." \\
        --seeds 424242,424243,424244 --out-dir ab-h3 --yes

Environment: FAL_KEY (or FAL_API_KEY, which is copied over).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

if "FAL_KEY" not in os.environ and "FAL_API_KEY" in os.environ:
    os.environ["FAL_KEY"] = os.environ["FAL_API_KEY"]

MODEL = "minimax/h3/reference-to-video"

# fal's realism LoRA, trained for people who hold up in close-up: skin keeps its texture
# instead of smoothing out. Served by the "/lora" variant of the endpoint, which takes a
# `loras` array; the base endpoint has no LoRA field. The adapter only touches attention
# projections shared by t2v/i2v/r2v, so it works on reference-to-video.
#
# Why this exists as an option: measured on the locked reference vs its own generations,
# the REFERENCE image carries good skin detail (pores, freckles, stubble) and the
# generated medium shot loses it, while a tight framing of the same character keeps it.
# The smoothing is a function of how many pixels land on the face, and 768P is the native
# ceiling (2K/4K only upscale a 768P base). This LoRA and tighter framing are the two
# levers that address that; prompt wording is not one of them.
REALISM_LORA = ("https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA/"
                "resolve/main/h3-realism-people-t2v-i2v-r2v.safetensors")
REALISM_TRIGGER = "r34l1sm"

# The fixed prompt for this format. Dialogue is the only per-episode variable.
#
# Written to H3's own conventions, which differ from Veo's in one way that matters:
# H3 wants audio directed POSITIVELY in a trailing "Sound:" clause -- everything before
# it describes the picture, everything after describes the track. Anything left unnamed
# is filled in by the model, and that is where an unwanted music bed comes from. A
# front-loaded ban list (the Veo pattern) does not close those paths. So the sound
# clause names exactly what should be heard, then shuts every musical path explicitly,
# the way H3's guidance recommends closing every voice path when you want no speech.
#
# Character identity is held by an explicit preservation list rather than by trusting
# the reference image alone -- also H3's documented recommendation.

PICTURE = (
    "SCENE: a raw unedited selfie video recorded on a phone, straight off the camera "
    "roll, unposted and never edited. "
    "Preserve exactly from Image 1, unchanged for the whole shot: his face and hairline, "
    "{look}, {garment}, {props}. The "
    "monitor displays ONLY the page shown in Image 1 and never changes. "
    "He is filming himself on a phone held in his right hand at arm's length, seated at "
    "the desk; his left hand is free and gestures naturally as he speaks. "
    "He blinks naturally and regularly throughout at a normal relaxed rate, both eyes "
    "closing and opening together and fully. His eyes hold their exact shape, colour and "
    "spacing from Image 1 for the whole shot, and stay looking into the camera lens. "
    "Never a half-blink or a frozen stare, never one eye at a time, never a flutter or a "
    "twitch; irises and pupils never change size, shape or colour, never smear or double, "
    "and the gaze never drifts off camera. "
    "{shake} "
    "{framing} "
    "Natural phone-camera look, ordinary indoor room lighting, no cinematic or "
    "professional lighting, no colour grading, no film grain, no stylization. "
    "Real-time, not slow motion. No on-screen text, no scene cuts."
)

# Framing. This is a REALISM lever, not just composition: measured on the locked
# reference against its own generations, skin texture survives in proportion to how many
# pixels land on the face, and 768P is the native ceiling. A tight framing of this
# character kept pores and blemishes that the medium framing smoothed away.
#
# The catch is the caption. The rejected take that first showed this effect was tight AND
# had the head high in frame, which put the fixed-Y caption box across his eyes. So
# "tight" here specifies head SIZE and head POSITION separately -- bigger face, still
# sitting low with wall above it -- rather than just asking to move closer.
FRAMING = {
    "medium":
        "Medium selfie shot: his head sits in the lower two thirds of the frame with "
        "empty wall visible above it, monitor on the left of frame, subject on the right.",
    "tight":
        "Tight selfie shot, phone closer to his face: his head and shoulders fill most of "
        "the frame and his face is large in it. His head still sits LOW, in the lower two "
        "thirds, with a clear band of empty wall above it -- do not crop the top of his "
        "head and do not raise him into the upper third. Monitor still visible on the "
        "left of frame, subject on the right.",
}

# Handheld-shake wording, calibrated as a ladder so "natural" can be settled by
# measurement instead of argued about. Two principles behind the phrasing:
#
#   1. Describe the CAUSE, not the amount. "Shaky" makes a model shake the camera;
#      "an arm holding a phone" makes it move the way an arm does. Real selfie footage
#      is mostly slow drift with occasional small corrections -- NOT constant jitter,
#      which is what "handheld shake" alone tends to produce.
#   2. Bound the failure modes explicitly, the same way the Sound: clause closes every
#      musical path. Whip pans, walking bounce, rhythmic wobble and drifting zooms are
#      all "handheld" to a model unless ruled out.
#
# Measure the result with ab_report.py, which reports global frame motion in px against
# a 1080-wide frame. For reference, every clip generated or shipped before this option
# existed measured 0.01-0.04 px median -- i.e. tripod-locked.
SHAKE = {
    "none":
        "Static locked-off camera on a tripod, no camera movement at all.",
    "subtle":
        "The camera is his phone, held in one outstretched hand: the frame breathes and "
        "shifts a little the whole time, the way an arm at arm's length cannot hold "
        "perfectly still. Small movements that SETTLE BACK -- his arm wanders a few "
        "centimetres and corrects, so the shot keeps returning to the same framing. The "
        "composition at the end of the clip is the same as at the start: he stays the "
        "same size in frame throughout. Never a steady drift in one direction, never "
        "gradually pulling back or pushing in, never getting wider or tighter over the "
        "clip. Never locked off on a tripod, and no pan, swing, whip, bounce or zoom.",
    "natural":
        "The camera is his phone, held in one outstretched hand: the frame drifts slowly "
        "and continuously as his arm tires and re-settles, with a few small corrections "
        "as he speaks and a faint rise and fall from his breathing. Unmistakably "
        "handheld, but relaxed and steady -- he is holding the phone still on purpose, "
        "not walking or gesturing with it. Never locked off on a tripod. No pans, no "
        "swings, no whips, no walking bounce, no rhythmic wobble, no zooming.",
    "loose":
        "The camera is his phone, held casually in one hand: the frame moves and "
        "re-frames noticeably throughout, drifting and correcting as his arm shifts, the "
        "way a real unedited selfie video looks. Clearly handheld and a little untidy, "
        "but still no pans, whips, walking bounce or zooming.",
}

# H3's audio sections, in its documented order:
#   Scene -> Speaker and Dialogue -> Diegetic Sound -> Overall Soundscape ->
#   Non-Diegetic Music -> Audio Restrictions
#
# THE POINT OF THIS SHAPE: H3 has a music slot whether you use it or not. Its own guidance
# is blunt about it -- "silence is never the default, so an undefined soundtrack is still
# a generated one" -- and the documented way to get no score is to fill the slot with N/A,
# not to add prohibitions elsewhere. Earlier versions of this prompt piled up nine "no
# music" phrases in a restrictions list while leaving the music field itself blank, and
# measured a 0.3 dB improvement across fifteen takes. The field is the mechanism; the
# ban list is not.
#
# The scene is also framed as an unedited camera-roll recording. Music comes from the
# model's prior about EDITED short-form video; describing the clip as raw footage that has
# never been through post attacks that prior directly, which no prohibition does.
SOUND = (
    "DIEGETIC SOUND: only what the phone microphone would pick up in the room -- his "
    "voice, the small movements of his shirt and chair as he shifts, the faint hollow "
    "handling sound of a phone held in one hand. Nothing else is audible. "
    "OVERALL SOUNDSCAPE: one small quiet carpeted home office with the window shut. A low "
    "neutral room tone sits under the whole clip at a constant level and never changes. "
    "The recording is a raw unedited clip straight off the camera roll: it has never been "
    "through an editor, nothing has been added to it in post, and it is not a finished "
    "video. "
    "NON-DIEGETIC MUSIC: N/A. None. There is no score, no soundtrack and no music cue in "
    "this clip at any point from the first frame to the last. "
    "AUDIO RESTRICTIONS: no music, no score, no soundtrack, no backing track, no "
    "instrumental bed, no beat, no percussion, no drums, no bassline, no synth pad, no "
    "drone, no melody, no chord, no single musical note, no hum, no singing, no jingle, "
    "no sound design, no whoosh, no riser, no sting, no transition effect, no applause, "
    "no crowd, no birds, no traffic, no second voice. "
    "MIX: his voice and the room tone are the entire soundtrack."
)

# Veo variant. Two differences that matter, beyond the endpoint:
#   - Veo takes ONE image and has no audio reference, so "Image 1"/"Audio 1" citation is
#     meaningless here and the voice cannot be pinned (critical knowledge #14).
#   - Veo's documented failure mode is the opposite of H3's: it responds to a music ban
#     stated at the START of the prompt (critical knowledge #2), where H3 needed a filled
#     music slot. Keep each model's own remedy; they are not interchangeable.
VEO_MODEL = "fal-ai/veo3.1/fast/image-to-video"
VEO_PREFIX = ("NO background music, NO soundtrack, NO score of any kind. Spoken voice and "
              "quiet room tone only. ")


# Veo needs a SHORT prompt. Porting the H3 prompt wholesale (~250 words of stacked
# constraint) produced two failures at once: he spoke a different line with gibberish
# words in it, and the monitor text warped. Both are instruction-dropping under load --
# long prompts dilute attention, and the two things that suffer first are the dialogue
# (buried at the end) and fine detail like rendered text.
#
# So this is rebuilt tight, with two structural changes:
#   1. The DIALOGUE COMES FIRST, in quotes, before any scene description. It is the one
#      thing that must not drift.
#   2. The monitor's heading is stated as literal text. Asking a model to "keep the screen
#      as in the reference" makes it re-invent the letterforms; naming the words gives it
#      something to hold onto.
VEO_MONITOR = ('a dark webpage headed "Growth superpowers for Claude Code" in white '
               'serif text, with a terminal window below it')

# Per-character defaults. The original series' character and room; a new character passes
# --look / --props / --monitor instead of editing these, so one script serves both shows
# and neither drifts into the other's description.
DEFAULT_LOOK = "short dark hair, light stubble"
DEFAULT_PROPS = ("the wooden desk, the black mesh office chair, the red-backlit "
                 "mechanical keyboard, and the window blinds")


def _sub_garment(s: str, garment: str) -> str:
    return s.replace("{garment}", garment)


PACE_EARLY = ("He delivers the line briskly and finishes speaking EARLY, with at least two "
              "full seconds of the clip remaining after his last word. He does not slow "
              "down, stretch words or add pauses to fill the time. ")


def veo_prompt(line: str, shake: str, framing: str, garment: str,
               finish_early: bool = False, look: str = DEFAULT_LOOK,
               monitor: str = VEO_MONITOR, action: str | None = None) -> str:
    # The body is concatenated, not formatted, so {garment} is substituted at the end.
    # Leaving it to .format() here would break on the literal braces elsewhere in the text.
    return _sub_garment(
        VEO_PREFIX +
        (PACE_EARLY if finish_early else '') +
        'A man speaks one line straight to camera: "' + line + '" '
        'He says exactly those words, once, with no other speech and no made-up words. '
        + ((action.rstrip() + ' ') if action else '') +
        'He talks the way a real person talks to their phone -- relaxed, natural pace, '
        'small natural pauses, blinking normally as anyone does mid-sentence. '
        'When the line is finished he stays on camera for a couple of seconds longer '
        'without speaking: he closes his mouth and gives a small satisfied smile, the way '
        'someone does at the end of a clip before they reach up to stop the recording. '
        'His eyes stay straight on the lens for every frame of this pause. He does NOT '
        'nod, does not tip his head, does not look down and does not glance away -- a nod '
        'drops the eyes off the lens, and the tail of the clip is what the cut lands on. '
        'He is the man in the reference image and looks exactly like him: same face, same '
        + look + ', same {garment}. He is seated at '
        'his wooden desk in a small home office, filming himself on a phone held in his '
        "right hand at arm's length"
        + (". " if action else ", his left hand gesturing as he talks. ") +
        # When a take has a SCRIPTED gesture, a standing instruction to gesture while
        # talking fights it: episode 1's hook was asked to raise a finger and also to
        # gesture throughout, and the model settled it by raising the finger in the first
        # second and holding it for three, so it was merely still up on the word rather
        # than landing on it. --action owns the hands, or nothing does.
        ""
        'Behind him on the left is his monitor showing ' + monitor + '. That text is '
        'already on the screen, stays perfectly sharp and completely unchanged for the '
        'whole shot, and never warps, flickers, scrambles or re-renders. '
        + shake + ' ' + framing + ' '
        'His eyes stay clear and keep their shape and colour from the reference image. '
        'Ordinary indoor lighting, natural phone-camera look, real time. '
        'Avoid: background music, soundtrack, score, any other dialogue, invented or '
        'nonsense words, subtitles, captions, on-screen text overlays, warped or changing '
        'screen text, camera moves, scene cuts, a second person.'
    , garment)


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}


def download(url: str, out: Path, min_bytes: int = 20000) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(out, "wb") as f:
        f.write(r.read())
    size = out.stat().st_size
    if size < min_bytes:
        raise SystemExit("download too small (%d bytes); upstream may be broken" % size)
    return size


def check_ext(paths: list, allowed: set, kind: str) -> None:
    for p in paths:
        if not p.exists():
            sys.exit("ERROR: no such file: %s" % p)
        if p.suffix.lower() not in allowed:
            sys.exit("ERROR: %s is not a recognised %s file (%s)" % (p, kind, p.suffix))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", action="append", type=Path, required=True,
                    help="locked character reference image; repeat for Image 2, 3, ...")
    ap.add_argument("--voice-ref", type=Path,
                    help="locked voice cut, becomes Audio 1 (2-15s, cut from a RAW take)")
    ap.add_argument("--video-ref", action="append", type=Path, default=[],
                    help="optional motion reference, becomes Video 1, 2, ...")
    ap.add_argument("--line", required=True, help="the dialogue for this take")
    ap.add_argument("--garment", default="grey henley t-shirt",
                    help="what he is wearing, to match the reference image. MUST match "
                         "the outfit variant being used or the prompt fights the picture "
                         "-- the default is eps 1-2 only (State 0b)")
    ap.add_argument("--action",
                    help="one extra sentence of performance direction, inserted right "
                         "after the line. Use it for a gesture that must land on a "
                         "specific word (e.g. holding up fingers for the episode "
                         "number). Keep it to ONE sentence naming the word it lands on")
    ap.add_argument("--look", default=DEFAULT_LOOK,
                    help="hair and facial hair, to match the locked image (default: the "
                         "original character). A new character MUST pass its own")
    ap.add_argument("--props", default=DEFAULT_PROPS,
                    help="the fixed room items to preserve, to match the locked image")
    ap.add_argument("--monitor", default=VEO_MONITOR,
                    help="what the monitor shows, named in words. Naming the words beats "
                         "'as in the reference', which makes it re-invent the letterforms")
    ap.add_argument("--seeds", required=True,
                    help="comma-separated seeds, one take each (e.g. 424242,424243,424244)")
    ap.add_argument("--out-dir", type=Path, default=Path("ab-h3"))
    ap.add_argument("--duration", type=int, default=6,
                    help="seconds. Veo accepts ONLY 4, 6 or 8 -- anything else is rejected "
                         "by the API with a literal_error, after the submit round trip. "
                         "The old help said 5-15, which is the H3 range")
    ap.add_argument("--resolution", default="768P", choices=["480P", "768P", "2K", "4K"],
                    help="480P and 768P are native generation modes; 2K and 4K upscale a "
                         "768P base, so they add polish rather than real detail -- and "
                         "polish reads as wrong for a phone-selfie format (default 768P)")
    ap.add_argument("--aspect-ratio", default="9:16")
    ap.add_argument("--prompt-expansion", default="balanced",
                    choices=["fast", "balanced", "quality"])
    ap.add_argument("--engine", default="h3", choices=["h3", "veo"],
                    help="h3 (reference-to-video, pins the voice, always generates music) "
                         "or veo (image-to-video, no voice pinning, much cleaner audio)")
    ap.add_argument("--framing", default="medium", choices=sorted(FRAMING),
                    help="medium (current locked look) or tight. Tight puts more pixels on "
                         "the face, which is what carries skin texture at 768P -- but "
                         "verify the caption band stays clear of his head")
    ap.add_argument("--shake", default="subtle", choices=sorted(SHAKE),
                    help="handheld camera wording. LOCKED at 'subtle' for this series -- "
                         "do not change it per episode; the whole series must feel like "
                         "one camera. Measured on the calibration ladder (2026-09-05): "
                         "subtle 1.86 px median / 5.07 max, natural 2.50 / 8.38, loose "
                         "2.84 / 20.59. Subtle wins on evenness, not on amount")
    ap.add_argument("--finish-early", action="store_true",
                    help="ask the model to finish the line early and hold. Veo paces "
                         "dialogue to FILL the duration (#24), which leaves no settle to "
                         "trim on: 13 takes on episode 5 all ran to within 0.24s of the "
                         "end and every one failed the end-frame check. Nothing in the "
                         "prompt asked it to stop early -- it asked him to stay on camera "
                         "AFTER the line, which is unreachable if the line never ends")
    ap.add_argument("--no-preamble", action="store_true",
                    help="send only --line, without the format's fixed requirements")
    ap.add_argument("--realism-lora", action="store_true",
                    help="use fal's H3 realism-people LoRA (keeps skin texture instead of "
                         "smoothing it). Switches to the endpoint's /lora variant and "
                         "prefixes the trigger word")
    ap.add_argument("--lora-scale", type=float, default=1.0,
                    help="LoRA strength; 1.0 is the intended value, 0.6-0.8 for a lighter "
                         "touch (default 1.0)")
    ap.add_argument("--model", default=MODEL, help="override the fal model id")
    ap.add_argument("--yes", action="store_true", help="actually submit; costs money")
    args = ap.parse_args()

    if not 5 <= args.duration <= 15:
        sys.exit("ERROR: duration must be 5-15s (got %d)" % args.duration)

    check_ext(args.image, IMAGE_EXT, "image")
    check_ext(args.video_ref, VIDEO_EXT, "video")
    if args.voice_ref:
        check_ext([args.voice_ref], AUDIO_EXT, "audio")
    elif not args.no_preamble:
        print("WARNING: no --voice-ref. The voice will be re-chosen per generation and "
              "will drift between takes -- that is the failure this harness exists to "
              "measure (critical knowledge #14).", file=sys.stderr)

    n_refs = len(args.image) + len(args.video_ref) + (1 if args.voice_ref else 0)
    if n_refs > 12:
        sys.exit("ERROR: %d reference files; H3 accepts at most 12" % n_refs)
    if args.voice_ref and not (args.image or args.video_ref):
        sys.exit("ERROR: an audio reference cannot be sent alone; it must accompany at "
                 "least one image or video reference")

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    if not seeds:
        sys.exit("ERROR: no seeds given")

    if args.no_preamble:
        prompt = args.line
    else:
        # Dialogue belongs to the picture half; the Sound: clause must come last.
        # Speaker-and-Dialogue section: H3 wants a labelled speaker and the exact line.
        prompt = (PICTURE.format(shake=SHAKE[args.shake], framing=FRAMING[args.framing],
                                 garment=args.garment, look=args.look, props=args.props)
                  + ' SPEAKER AND DIALOGUE: Speaker 1 is the man in the reference image, the '
                    'only person present and the only voice in the clip. Speaking calmly '
                    'and directly to the lens, he says the line once, then stops talking '
                    'and holds still with his mouth closed: "' + args.line + '" '
                  + SOUND)
    if args.engine == "veo":
        if not args.no_preamble:
            prompt = veo_prompt(args.line, SHAKE[args.shake], FRAMING[args.framing],
                                args.garment, finish_early=args.finish_early,
                                look=args.look, monitor=args.monitor,
                                action=args.action)
        if args.model == MODEL:
            args.model = VEO_MODEL
        if args.realism_lora:
            sys.exit("ERROR: the realism LoRA is an H3 adapter; it does not apply to Veo.")
        if args.voice_ref:
            print("NOTE: Veo has no audio reference input. The voice reference will NOT be "
                  "sent and the voice will vary per take -- screen it with ab_report.py "
                  "(critical knowledge #14).", file=sys.stderr)
    elif args.realism_lora:
        prompt = REALISM_TRIGGER + ", " + prompt
        if args.model == MODEL:
            args.model = MODEL + "/lora"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

    print("model        %s" % args.model)
    print("takes        %d (seeds %s)" % (len(seeds), ", ".join(str(s) for s in seeds)))
    print("duration     %ds at %s, %s" % (args.duration, args.resolution, args.aspect_ratio))
    print("engine       %s" % args.engine)
    print("shake        %s" % args.shake)
    print("framing      %s" % args.framing)
    print("realism lora %s" % ("scale %.2f" % args.lora_scale if args.realism_lora else "off"))
    print("images       %s" % ", ".join(p.name for p in args.image))
    print("video refs   %s" % (", ".join(p.name for p in args.video_ref) or "-"))
    print("voice ref    %s" % (args.voice_ref.name if args.voice_ref else "- (voice will drift)"))
    print("prompt hash  %s" % prompt_hash)
    print("out dir      %s" % args.out_dir)
    print("\nprompt:\n%s\n" % prompt)

    if not args.yes:
        print("Dry run. Re-run with --yes to submit %d paid generations." % len(seeds))
        return 0

    try:
        from fal_client import subscribe, upload_file  # type: ignore
    except ImportError:
        sys.exit("ERROR: fal_client not installed. `pip install fal-client`.")
    if not os.environ.get("FAL_KEY"):
        sys.exit("ERROR: FAL_KEY (or FAL_API_KEY) not set.")

    print("uploading references ...")
    image_urls = [upload_file(str(p)) for p in args.image]
    video_urls = [upload_file(str(p)) for p in args.video_ref]
    audio_urls = [upload_file(str(args.voice_ref))] if args.voice_ref else []

    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for seed in seeds:
        # A wave is PAID per seed, and a wave can die part way through -- episode 1's
        # hook wave lost its network on the third seed after two had been generated and
        # billed. Without this, the re-run pays for those two a second time. A take
        # already on disk for this seed is the take; seeds are deterministic.
        done = args.out_dir / ("%s-seed%d.mp4" % ("veo" if args.engine == "veo" else "h3", seed))
        if done.exists() and done.stat().st_size > 200_000:
            print("\nseed %d -> already on disk (%.1f MB), not re-paying"
                  % (seed, done.stat().st_size / 1e6))
            manifest.append({"seed": seed, "status": "ok", "file": str(done),
                             "reused": True})
            continue
        if args.engine == "veo":
            payload = {
                "prompt": prompt,
                "image_url": image_urls[0],
                "duration": "%ds" % args.duration,
                "resolution": "720p",
                "aspect_ratio": args.aspect_ratio,
                "generate_audio": True,
                "seed": seed,
            }
            print("\nseed %d -> submitting ..." % seed)
            try:
                result = subscribe(args.model, arguments=payload, with_logs=False)
            except Exception as e:  # noqa: BLE001
                print("  FAILED: %s" % e, file=sys.stderr)
                manifest.append({"seed": seed, "status": "failed", "error": str(e)})
                continue
            url = ((result or {}).get("video") or {}).get("url")
            if not url:
                print("  FAILED: no video url: %s" % json.dumps(result)[:300], file=sys.stderr)
                manifest.append({"seed": seed, "status": "no_output"})
                continue
            out = args.out_dir / ("veo-seed%d.mp4" % seed)
            size = download(url, out)
            print("  -> %s (%.1f MB)" % (out, size / 1e6))
            manifest.append({"seed": seed, "status": "ok", "file": str(out),
                             "prompt_hash": prompt_hash, "model": args.model,
                             "engine": "veo", "shake": args.shake,
                             "framing": args.framing})
            continue

        payload = {
            "prompt": prompt,
            "duration": args.duration,
            "resolution": args.resolution,
            "aspect_ratio": args.aspect_ratio,
            "prompt_expansion_mode": args.prompt_expansion,
            "seed": seed,
            "reference_image_urls": image_urls,
        }
        if video_urls:
            payload["reference_video_urls"] = video_urls
        if audio_urls:
            payload["reference_audio_urls"] = audio_urls
        if args.realism_lora:
            payload["loras"] = [{"path": REALISM_LORA, "scale": args.lora_scale}]

        print("\nseed %d -> submitting ..." % seed)
        try:
            result = subscribe(args.model, arguments=payload, with_logs=False)
        except Exception as e:  # noqa: BLE001 - report and keep going to the next seed
            print("  FAILED: %s" % e, file=sys.stderr)
            manifest.append({"seed": seed, "status": "failed", "error": str(e)})
            continue

        url = ((result or {}).get("video") or {}).get("url")
        if not url:
            print("  FAILED: no video url in response: %s" % json.dumps(result)[:400],
                  file=sys.stderr)
            manifest.append({"seed": seed, "status": "no_output", "response": result})
            continue

        out = args.out_dir / ("h3-seed%d.mp4" % seed)
        size = download(url, out)
        print("  -> %s (%.1f MB)" % (out, size / 1e6))
        manifest.append({"seed": seed, "status": "ok", "file": str(out),
                         "prompt_hash": prompt_hash, "model": args.model,
                         "duration": args.duration, "resolution": args.resolution,
                         "shake": args.shake, "framing": args.framing,
                         "realism_lora": args.lora_scale if args.realism_lora else None,
                         "voice_ref": str(args.voice_ref) if args.voice_ref else None})

    mpath = args.out_dir / "manifest.json"
    mpath.write_text(json.dumps(
        {"model": args.model, "prompt": prompt, "prompt_hash": prompt_hash,
         "takes": manifest}, indent=2), encoding="utf-8")

    ok = [m for m in manifest if m["status"] == "ok"]
    print("\n%d/%d takes generated. Manifest -> %s" % (len(ok), len(seeds), mpath))
    print("Copy the seed and prompt hash into TAKES.md, then measure:")
    print("  python ab_report.py %s <veo-control.mp4> --frames-dir ab-frames"
          % " ".join(m["file"] for m in ok))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
