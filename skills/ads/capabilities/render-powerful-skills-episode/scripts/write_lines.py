#!/usr/bin/env python3
"""Write an episode's four spoken lines from the skill's real demo. No human.

    python scripts/write_lines.py --series projects/<series> --check-shipped
    python scripts/write_lines.py --series projects/<series> --skill "Find warm intros" --part 12
    python scripts/write_lines.py --series projects/<series> --transcript t.json --part 12 \
        --out projects/<series>/episode-12/episode.json

The four lines were the one thing make_episode.py still refused to run without. They are
authoring, and they have to be TRUE to the run on screen -- VO A names the command the viewer
types, VO B names what comes back -- so they are written from skill-transcript.js's capture of
the real demo, never from the skill's name.

THE GATES are the operator's rules and the dialogue formula. Run --check-shipped first: every
gate is applied to every approved line, and a gate that fails an approved line is a wrong gate.
That check has already corrected two of them:

  - Episodes 1 and 2 PREDATE the formula. They read "just type:" with a colon and one closer
    has no "and see". The standard starts at episode 3, so --since defaults to 3.
  - Apostrophes are not contractions. Episode 5's "competitors' pricing" and "last month's"
    are possessives the operator approved; the first version banned every apostrophe.

It also found a real slip rather than a wrong gate: episode 8 ships "what you're missing"
twice, a contraction the operator's own rule forbids. That episode fails, and should.

  formula     hook "This skill literally turns Claude into your own ...";
              VO A "Install Gooseworks from their site with one command. Then inside Claude
              Code, just type, ...";  VO B "It ...";  closer "Go ..., and see ...".
  commas      never a colon.
  full words  no contractions. Possessives are fine.
  VO A        never reads out the slash command or the skill's hyphenated name.
  no people   never a real person, a handle or a URL from the demo. Find warm intros runs
              against a real LinkedIn profile and ends by naming three real people.
  length      each line's word count inside the range the formula-era episodes used. The
              edit has to fit the voiceover (#70, #85), and those episodes are the record.
  dashes      none.

The episode's outfit follows the series' own rotation: the same outfit for two episodes, then
the next one in the order the series first used them.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
FIELDS = ("hook_line", "vo_line_a", "vo_line_b", "closer_line")
PREFIX = {
    "hook_line": "This skill literally turns Claude into your own ",
    "vo_line_a": "Install Gooseworks from their site with one command. Then inside Claude Code, just type, ",
    "vo_line_b": "It ",
    "closer_line": "Go ",
}
CURLY_APOSTROPHE = chr(0x2019)
DASHES = (chr(0x2014), chr(0x2013))
CONTRACTION = re.compile(
    r"\b\w+'(?:re|ve|ll|d|m)\b|\b\w+n't\b|\b(?:it|that|there|what|who|here|he|she|let)'s\b",
    re.I)
URL = re.compile(r"https?://|www\.|\.com/", re.I)
# Words a line must stay free to use even when the demo mentions them in a possessive.
NOT_A_PERSON = {"Claude", "Gooseworks", "Goose", "LinkedIn", "Apollo", "Crustdata"}


def slug(t):
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", str(t).lower()))


def env():
    out = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r'\s*(?:export\s+)?([A-Z_]+)\s*=\s*"?([^"\s]+)"?', line)
            if m and not line.strip().startswith("#"):
                out[m.group(1)] = m.group(2)
    out.update({k: v for k, v in os.environ.items() if k.startswith("GOOSE_AEO_")})
    return out


def chat(e, messages, model):
    base = e.get("GOOSE_AEO_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 600,
            "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer %s" % e["GOOSE_AEO_OPENAI_API_KEY"],
                 "Content-Type": "application/json", "User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as ex:
            if ex.code != 429 or attempt == 4:
                raise
            print("        rate limited, waiting %ds ..." % (20 * (attempt + 1)))
            time.sleep(20 * (attempt + 1))


def shipped(series):
    eps = []
    for f in pathlib.Path(series).glob("episode-*/episode.json"):
        n = int(re.search(r"episode-(\d+)", str(f)).group(1))
        eps.append((n, json.loads(f.read_text(encoding="utf-8"))))
    return sorted(eps, key=lambda x: x[0])


def word_ranges(eps):
    """Word counts the shipped episodes used, widened when the sample is thin.

    min..max over one or two episodes is not a range, it is a target. On a new series
    the filter left exactly ONE episode in the sample, so vo_line_b had to be 21 words
    exactly; the writer burned all six retries missing it by two or three and the run
    died with the lines already written. Fewer than three samples, or a span under
    three, gets padded by three either way.
    """
    out = {}
    for k in FIELDS:
        counts = [len(e[k].split()) for _, e in eps if e.get(k)]
        if not counts:
            continue
        lo, hi = min(counts), max(counts)
        if len(counts) < 3 or hi - lo < 3:
            lo, hi = max(4, lo - 3), hi + 3
        out[k] = (lo, hi)
    return out


def names_in(t):
    """People and handles the demo mentions, which a spoken line must never repeat."""
    out = set()
    for m in re.finditer(r"linkedin\.com/in/([A-Za-z0-9-]+)", t.get("command") or ""):
        out.add(m.group(1))
    for line in list(t.get("transcript", [])) + [t.get("result") or ""]:
        m = re.match(r"^-\s+([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+)*)\s*\(", line)
        if m:
            out.add(m.group(1))
            out.update(p for p in m.group(1).split() if len(p) > 2)
        for m in re.finditer(r"\b([A-Z][a-z]{2,})'s\b", line):
            out.add(m.group(1))
        for m in re.finditer(r"\bto ([A-Z][a-z]{2,})\b", line):
            out.add(m.group(1))
    return sorted(n for n in out if n not in NOT_A_PERSON)


def problems(lines, ranges, skill_slug=None, tool=None, avoid=()):
    """Every failure as a sentence the model can act on, plus the failing field names."""
    out, bad = [], set()
    for k in FIELDS:
        s = (lines.get(k) or "").strip()
        if not s:
            out.append("%s is empty" % k)
            bad.add(k)
            continue
        if not s.startswith(PREFIX[k]):
            out.append("%s must start with %r" % (k, PREFIX[k]))
            bad.add(k)
        if k == "closer_line" and ", and see " not in s:
            out.append("closer_line must follow 'Go [action], and see [what you learn].'")
            bad.add(k)
        if not s.endswith("."):
            out.append("%s must end with a full stop" % k)
            bad.add(k)
        if ":" in s:
            out.append("%s has a colon. Commas, never colons." % k)
            bad.add(k)
        if CONTRACTION.search(s.replace(CURLY_APOSTROPHE, "'")):
            out.append("%s has a contraction. Full words only." % k)
            bad.add(k)
        if URL.search(s):
            out.append("%s reads out a URL." % k)
            bad.add(k)
        if any(d in s for d in DASHES):
            out.append("%s has a dash. Never use one." % k)
            bad.add(k)
        if k in ranges:
            lo, hi = ranges[k]
            n = len(s.split())
            if not lo <= n <= hi:
                out.append("%s is %d words; formula-era episodes used %d to %d" % (k, n, lo, hi))
                bad.add(k)
    a = (lines.get("vo_line_a") or "").lower()
    if "/gooseworks" in a:
        out.append("vo_line_a reads out the slash command; say what the command does instead")
        bad.add("vo_line_a")
    for name in (skill_slug, tool):
        if name and "-" in name and name.lower() in a:
            out.append("vo_line_a names the skill %r; say what it does instead" % name)
            bad.add("vo_line_a")
    for name in avoid:
        for k in FIELDS:
            if re.search(r"\b%s\b" % re.escape(name), lines.get(k) or "", re.I):
                out.append("%s names %r from the demo. Never name a real person or handle."
                           % (k, name))
                bad.add(k)
    return out, bad


def next_outfit(eps):
    """Two episodes per outfit, then the next in the order the series first used them."""
    seq = [(e.get("outfit") or "locked") for _, e in eps]
    if not seq:
        return "locked", None
    cycle = list(dict.fromkeys(seq))
    if len(seq) >= 2 and seq[-1] != seq[-2]:
        o = seq[-1]
    else:
        o = cycle[(cycle.index(seq[-1]) + 1) % len(cycle)]
    garment = next((e.get("garment") for _, e in reversed(eps)
                    if (e.get("outfit") or "locked") == o and e.get("garment")), None)
    return o, garment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True)
    ap.add_argument("--check-shipped", action="store_true",
                    help="run every gate on the approved episodes and stop")
    ap.add_argument("--allow-names", action="store_true",
                    help="let the lines name the real people the demo mentions. OFF by "
                         "default: the series does not put someone's name in an ad "
                         "without a reason to. Check the clone actually says the names "
                         "before turning it on.")
    ap.add_argument("--since", type=int, default=3,
                    help="first episode written to the current formula")
    ap.add_argument("--skill", help="skill label or slug; captured live with skill-transcript.js")
    ap.add_argument("--transcript", help="a saved skill-transcript.js output instead")
    ap.add_argument("--part", type=int)
    ap.add_argument("--out", help="write episode.json here (refuses to overwrite)")
    ap.add_argument("--model", default="qwen/qwen3.8-27b")
    ap.add_argument("--tries", type=int, default=6)
    a = ap.parse_args()

    eps = shipped(a.series)                                  # all of them, for the rotation
    # --since counts PARTS, not folders. This series keeps part 1 in episode-2, so a
    # folder-number filter left a single episode in the sample and collapsed every range
    # onto one number. Compare against the part the episode declares where it has one.
    def _part(x):
        return x[1].get("part_number") if isinstance(x[1].get("part_number"), int) else x[0]

    in_formula = [x for x in eps if _part(x) >= a.since]
    ranges = word_ranges(in_formula)

    if a.check_shipped:
        print("word ranges from episodes %d+: %s" % (a.since, ranges))
        failing = 0
        for n, e in eps:
            if n < a.since:
                print("  episode %-3d predates the formula, not checked" % n)
                continue
            ps, _ = problems(e, ranges, e.get("featured_skill"), e.get("screen_skill"))
            print("  episode %-3d %s" % (n, "passes" if not ps else "FAILS"))
            for p in ps:
                print("      - %s" % p)
            failing += bool(ps)
        sys.exit(1 if failing else 0)

    if a.transcript:
        t = json.loads(pathlib.Path(a.transcript).read_text(encoding="utf-8"))
    elif a.skill:
        r = subprocess.run(["node", str(HERE / "skill-transcript.js"), a.skill],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            sys.exit("could not capture the demo:\n" + r.stderr[-600:])
        t = json.loads(r.stdout)
    else:
        sys.exit("give --skill or --transcript")
    if not t.get("command") or not t.get("result"):
        sys.exit("the transcript has no command or no result; cannot write true lines from it")

    e = env()
    if not e.get("GOOSE_AEO_OPENAI_API_KEY"):
        sys.exit("no GOOSE_AEO_OPENAI_API_KEY in .env")
    skill_slug = slug(t["skill"])
    avoid = names_in(t)
    if a.allow_names:
        if avoid:
            print("[lines] --allow-names: the lines MAY say %s" % ", ".join(sorted(avoid)))
        avoid = set()
    elif avoid:
        print("[lines] never to be spoken: %s" % ", ".join(sorted(avoid)))
    steps = [l for l in t.get("transcript", []) if re.match(r"^[A-Za-z][\w-]*\(", l)
             or re.match(r"^(Found|Enriched|Extracted|Wrote|Rendered|Pulled|\d)", l)]
    examples = [{k: ep[k] for k in FIELDS} for x in in_formula for ep in [x[1]]][-3:]
    # Naming a real person in an ad is a decision, so it is a switch and it is OFF by
    # default. Episode 3 turned it on deliberately, after checking the clone actually
    # says the three names rather than mangling them (#133).
    name_rule = (
        "- Name the people the demo names, spelled exactly as the demo spells them. "
        "Never a handle or a URL.\n" if a.allow_names else
        "- Never name a real person, a handle or a URL from the demo.\n")
    sysmsg = (
        "You write the four spoken lines of a 22-second vertical video that demos one "
        "Gooseworks skill inside Claude Code.\n"
        "Formula, exactly:\n"
        "- hook_line: \"This skill literally turns Claude into your own [X].\"\n"
        "- vo_line_a: \"Install Gooseworks from their site with one command. Then inside Claude "
        "Code, just type, [what the viewer types, in plain words].\"\n"
        "- vo_line_b: \"It [what it does], then [the result it produces].\"\n"
        "- closer_line: \"Go [action], and see [what you learn].\"\n"
        "Rules you must not break:\n"
        "- Describe only what the DEMO below shows. Invent nothing.\n"
        "- Full words. No contractions.\n"
        "- Commas, never colons. No dashes.\n"
        + name_rule +
        "- vo_line_a says what the command DOES in plain words; never read out the slash "
        "command, and never name the skill by its hyphenated name.\n"
        "- vo_line_c is spoken over the RESULTS section, which shows the file the run "
        "wrote. Say only the headline figures that appear in the demo, in the order they "
        "appear, 12 to 20 words. Write every number as WORDS, never digits, because it is "
        "read aloud: forty two thousand in spend, three point one times return. Invent no "
        "figure that is not in the demo.\n"
        "- Word counts per line: %s.\n"
        "Reply as JSON with keys hook_line, vo_line_a, vo_line_b, vo_line_c, closer_line, "
        "nothing else."
        % ", ".join("%s %d-%d" % (k, lo, hi) for k, (lo, hi) in ranges.items()))
    user = ("THE DEMO\nskill: %s\ncommand typed: %s\nwhat it did: %s\nresult: %s\n\n"
            "APPROVED LINES FROM EARLIER EPISODES, for voice and length only:\n%s"
            % (t["skill"], t["command"], " | ".join(steps[:8]), t["result"],
               json.dumps(examples, indent=1)))
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]

    lines, probs, keep = {}, ["no attempt"], {}
    for attempt in range(1, a.tries + 1):
        raw = chat(e, msgs, a.model)
        try:
            got = json.loads(raw)
        except json.JSONDecodeError:
            msgs.append({"role": "user", "content": "Not valid JSON. Reply with only the JSON."})
            continue
        lines = {k: (keep.get(k) or got.get(k, "")) for k in FIELDS}
        # vo_line_c is the v3 results line, spoken over the file the run wrote. It is
        # checked on its own terms rather than through problems(), which encodes the
        # four-line formula and has no prefix to match this against. Written here rather
        # than left to a person: both @gooseaitools episodes needed it and both got it by
        # hand, which is a step that silently does not happen when someone is in a hurry.
        c = (keep.get("vo_line_c") or got.get("vo_line_c") or "").strip()
        if c:
            c_bad = []
            if any(ch.isdigit() for ch in c):
                c_bad.append("vo_line_c must spell numbers as words, not digits")
            if any(d in c for d in DASHES):
                c_bad.append("vo_line_c must not use a dash")
            if CONTRACTION.search(c):
                c_bad.append("vo_line_c must use full words, no contractions")
            if not 10 <= len(c.split()) <= 22:
                c_bad.append("vo_line_c must be 12 to 20 words")
            if c_bad:
                extra_c = c_bad
            else:
                extra_c = []
                keep["vo_line_c"] = c
                lines["vo_line_c"] = c
        else:
            extra_c = ["vo_line_c is missing"]
        probs, bad = problems(lines, ranges, skill_slug, None, avoid)
        if extra_c:
            probs = probs + extra_c
            bad = bad | {"vo_line_c"} if isinstance(bad, set) else list(bad) + ["vo_line_c"]
        keep = {k: lines[k] for k in FIELDS if k not in bad}
        if lines.get("vo_line_c") and "vo_line_c" not in bad:
            keep["vo_line_c"] = lines["vo_line_c"]
        print("[try %d] %s" % (attempt, "PASSES" if not probs else "%d problem(s)" % len(probs)))
        for p in probs:
            print("        - %s" % p)
        if not probs:
            break
        msgs = msgs[:2] + [
            {"role": "assistant", "content": json.dumps(lines)},
            {"role": "user", "content":
             "These fields are FINAL, return them unchanged: %s\nRewrite only %s, fixing:\n- %s\n"
             "Return the full JSON." % (json.dumps(keep), ", ".join(sorted(bad)),
                                       "\n- ".join(probs))}]

    outfit, garment = next_outfit(eps)
    ref = ("locked/character-selfie.png" if outfit == "locked"
           else "outfits/%s/character-selfie.png" % outfit)
    part = a.part or ((eps[-1][0] + 1) if eps else 1)
    spec = {"part_number": part, "featured_skill": skill_slug, "outfit": outfit,
            "outfit_hook_ref": ref, "outfit_closer_ref": ref}
    if garment:
        spec["garment"] = garment
    spec.update(lines)
    spec.update({"demo_command": t["command"], "demo_result": t["result"],
                 "written_by": "write_lines.py", "gates_passed": not probs})

    print("\n" + json.dumps(spec, indent=1, ensure_ascii=False))
    if a.out:
        dst = pathlib.Path(a.out)
        if dst.exists():
            sys.exit("[lines] %s exists; not overwriting an episode spec" % dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
        print("[lines] wrote %s" % dst)
    sys.exit(0 if not probs else 1)


if __name__ == "__main__":
    main()
