#!/usr/bin/env python3
"""Plan the creator's talking takes and write their prompts. Free, no network.

    plan_takes.py --beats cutlist.json --character character.json --out work/takes \
        [--mannerism mannerism.mp4] [--aspect 9:16] [--resolution 1080P]

--beats is any file with `beats: [{id, start, end, vo}]` (the footage-cutlist cut list
works as-is). --character is:

    {"image": "character.png",               # the approved still, local path
     "identity": "a man in his late 20s ...", # who they are, VERBATIM into every take
     "environment": "a sunlit apartment ...", # the room, VERBATIM into every take
     "delivery": "optional: how they speak"}

Writes <out>/takes.json (run_takes.py's spec) and <out>/<id>-prompt.txt per take.

TAKES SPLIT ON LINE BOUNDARIES, never inside a line. H3 caps a take at 15s, so a 30s
script is two or three takes. A join between two lines can be hidden with a 0.10s
dissolve (join_takes.py); a join mid-sentence cannot.

EACH TAKE RUNS 0.6s PAST ITS LAST WORD, rounded up to whole seconds (H3 takes whole
seconds, 5-15). A take planned to exactly its words clipped the last word of a reel.

THE PROMPT is the one that held identity and eyeline across the reference builds. The
person and the room come from character.json word for word, so every take describes the
same person the same way; nothing about a person is written here. Square brackets in a
line are refused: H3 speaks them aloud.

ASPECT: 9:16 when the creator ever fills the frame, otherwise the closest H3 ratio to the
creator's zone. Cropping a 16:9 take to 9:16 is a 2.5x upscale.
"""
import argparse
import hashlib
import json
import math
import pathlib
import re

MAX_TAKE = 15
MIN_TAKE = 5
MAX_SPEECH = 14.2
TAIL = 0.6
RATIOS = {"21:9": 21 / 9, "16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "3:4": 3 / 4, "9:16": 9 / 16}

DEFAULT_DELIVERY = ("energetic and certain, telling a friend about something just found. Not presenting, "
                    "not announcing, not reading. Sentences run together with almost no gap. Pitch falls on "
                    "the last word of each sentence. Consonants relaxed, volume varying word to word.")

TEMPLATE = """A single continuous locked-off medium shot of <Subject 1>, filmed on a phone propped on a stand.

[Shot 1] The phone is PROPPED ON A STAND at eye level and the person sits in front of it. Chest-up framing, both shoulders in frame, head roughly centred with a little headroom, shot straight on. The hands are FREE and gesture while talking, one coming up and settling. Nobody holds the phone, there is no arm extended toward the camera. THE CAMERA DOES NOT MOVE: locked off, no handheld drift, no pan, no zoom, no reframing. One continuous unbroken shot.

EYES STAY ON THE CAMERA LENS FOR THE WHOLE CLIP, from the very first frame to the very last, including the final word. Never glancing away, never looking down, never letting the gaze drift off the lens at the end of a sentence.

THE SUBJECT AND THE SET, unchanged from the first frame to the last, exactly as <Picture 1>: {identity}
{environment}
SKIN matches <Picture 1> exactly: visible pores, no smoothing, no plastic gloss, no over-sharpened edges. The light is the light in <Picture 1> and it does not change.
{mannerism}
HOW THEY SPEAK: {delivery}
DIEGETIC SOUND: the voice and quiet room tone only.
AUDIO RESTRICTIONS: no music, no beat, no sound design, no second voice, and no robotic, synthetic, text-to-speech, monotone or announcer-like delivery. The spoken audio contains ONLY the words inside the dialogue block. No stage directions and no markup is ever spoken aloud.

<Subject 1> (S1) says, <d>[English] <inhale> {dialogue}</d>

Quiet room around the voice. No music, no sound design, no second voice.
"""

MANNERISM = """
<Video 1> is a filmed reference person recorded the same way. Take from it ONLY the rhythm of natural speech, where the pauses fall and where the pace speeds up, and the small involuntary movement while talking: the head settling, the blink rate, the eyebrow lifts, the hands coming up and dropping back. Do NOT take the face, hair, clothes, room, lighting, framing or eyeline. The person is <Subject 1> from <Picture 1> and nobody else.
"""


def split(beats):
    takes, cur = [], []
    for b in beats:
        if cur and b["end"] - cur[0]["start"] > MAX_SPEECH:
            takes.append(cur)
            cur = []
        cur.append(b)
    if cur:
        takes.append(cur)
    for t in takes:
        if t[-1]["end"] - t[0]["start"] > MAX_SPEECH:
            raise SystemExit("line %s alone runs %.1fs, over one take; split or shorten it"
                             % (t[0]["id"], t[-1]["end"] - t[0]["start"]))
    return takes


def seed(slug, tid):
    return 300000 + int(hashlib.sha256(("%s-%s" % (slug, tid)).encode()).hexdigest(), 16) % 600000


def pick_aspect(spec):
    W, H = spec.get("size", [1080, 1920])
    if any(b.get("state") == "creator" for b in spec["beats"]) or not spec.get("seam"):
        return "9:16"
    seam = spec["seam"]
    zh = (H - seam) if spec.get("creator_side", "bottom") == "bottom" else seam
    ar = W / float(zh)
    return min(RATIOS, key=lambda k: abs(math.log(RATIOS[k] / ar)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beats", required=True)
    ap.add_argument("--character", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mannerism", help="optional muted motion-reference clip (2-15s, rights cleared)")
    ap.add_argument("--aspect", choices=sorted(RATIOS))
    ap.add_argument("--resolution", default="1080P", choices=["480P", "768P", "1080P"])
    ap.add_argument("--slug", default="creator")
    a = ap.parse_args()

    spec = json.loads(pathlib.Path(a.beats).read_text(encoding="utf-8"))
    beats = [b for b in spec["beats"] if (b.get("vo") or "").strip()]
    if not beats:
        raise SystemExit("no beat has a `vo` line")
    chp = pathlib.Path(a.character)
    ch = json.loads(chp.read_text(encoding="utf-8"))
    img = pathlib.Path(ch["image"])
    img = img if img.is_absolute() else (chp.parent / img)
    if not img.exists():
        raise SystemExit("character image not found: %s" % img)
    if not (ch.get("identity") or "").strip():
        raise SystemExit("character.json needs `identity`: the person, in the words that made the image")
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    plan = []
    for n, group in enumerate(split(beats), 1):
        tid = "t%d" % n
        start, end = group[0]["start"], group[-1]["end"]
        dialogue = " <pause> ".join(re.sub(r"\s+", " ", b["vo"]).strip() for b in group)
        if re.search(r"\[[^\]]*\]", dialogue):
            raise SystemExit("take %s has square brackets in its lines, which H3 speaks aloud" % tid)
        prompt = TEMPLATE.format(identity=ch["identity"].strip(),
                                 environment=(ch.get("environment") or "").strip(),
                                 mannerism=MANNERISM if a.mannerism else "",
                                 delivery=(ch.get("delivery") or DEFAULT_DELIVERY).strip(),
                                 dialogue=dialogue)
        (out / ("%s-prompt.txt" % tid)).write_text(prompt, encoding="utf-8")
        plan.append({"id": tid, "seed": seed(a.slug, tid),
                     "dur": max(MIN_TAKE, min(MAX_TAKE, math.ceil(end - start + TAIL))),
                     "covers": [round(start, 2), round(end, 2)],
                     "beats": [b["id"] for b in group]})

    takes = {"model": "minimax/h3-max/reference-to-video", "char": str(img.resolve()),
             "mann": str(pathlib.Path(a.mannerism).resolve()) if a.mannerism else None,
             "slug": a.slug, "out": str(out.resolve()), "resolution": a.resolution,
             "aspect_ratio": a.aspect or pick_aspect(spec), "takes": plan}
    (out / "takes.json").write_text(json.dumps(takes, indent=2), encoding="utf-8")
    for t in plan:
        print("  %s seed %d  %2ds  covers %.2f-%.2f  (%s)" % (t["id"], t["seed"], t["dur"], t["covers"][0],
                                                          t["covers"][1], ",".join(t["beats"])))
    print("[plan] %s  %s %s, %d takes, %ds generated" % (out / "takes.json", takes["aspect_ratio"],
                                                      a.resolution, len(plan), sum(t["dur"] for t in plan)))


if __name__ == "__main__":
    main()
