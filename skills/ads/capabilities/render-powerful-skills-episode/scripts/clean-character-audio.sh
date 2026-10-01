#!/usr/bin/env bash
# clean-character-audio.sh — deterministic audio cleanup for character clips.
#
# WHY THIS EXISTS
# Veo bakes a room-ambience bed under the dialogue. The prompt cannot remove it:
# the prohibition was escalated to three placements plus an explicit "keep the
# background close to silence between phrases" instruction, and the measured noise
# floor did not improve — it varied randomly between takes from identical prompts
# (-66.3 / -41.8 / -34.4 dB). It is learned behaviour, not an instruction failure.
# Remove it here instead.
#
# v2 — REVISED AFTER A REAL REGRESSION
# v1 used afftdn nr=28 plus a hard downward expander (-30/-75). It removed the bed
# but was audibly duller, and v1's guard missed it. The guard measured broadband RMS
# in the 300-3400 Hz voice band, which stayed flat at -22.9 dB — while the bands that
# actually carry intelligibility collapsed:
#     HF 4-8 kHz        -21.1 -> -25.5 dB relative to voice   (4.4 dB lost)
#     sibilance 8-14kHz -26.7 -> -32.4 dB relative to voice   (5.7 dB lost)
# Cause: consonants and sibilants are high-frequency AND low-energy (-32..-40 dB), so
# a -30 dB expander threshold pulled down exactly the sounds that carry clarity.
# Lesson: measure clarity as HF energy RELATIVE TO the voice band, never as voice-band
# RMS alone.
#
# THE CHAIN
#   1. highpass=f=85     rumble; sits below male speech fundamentals (~85-180 Hz)
#   2. volume=<pre-gain> PER-TAKE level normalisation to a fixed -20 dB voice-band
#                        target. This is why thresholds no longer need hand-tuning per
#                        take: every clip enters the rest of the chain at the same
#                        level, so the fixed thresholds below always land in the same
#                        place relative to the speech.
#   3. afftdn nr=10      gentle spectral denoise (was 28). Dropping it to 10 costs
#                        almost no extra bed but recovers most of the clarity.
#   4. acompressor       evens the dynamics — this is the leveling pass.
#   5. agate (release    closes only in sustained gaps. A gate with a long release
#      400ms, range .05) stays open through speech, so unlike the v1 expander it does
#                        not chop consonants mid-word. Attenuation capped (range) so
#                        gaps duck rather than slam to silence, which avoids pumping.
#   6. loudnorm          final consistent loudness target.
#   7. alimiter          peak safety.
#
# MEASURED (hook, seed 424242, 6s):
#   HF rel        -22.1 -> -20.9 dB   (clarity slightly BETTER than source)
#   sibilance rel -28.0 -> -26.8 dB   (better than source)
#   level stddev    7.33 -> 3.21      (dynamics less than half as uneven)
#
# Video is stream-copied. Only the audio track is rewritten.
#
# Usage:
#   bash clean-character-audio.sh <input.mp4> [output.mp4]
# With one argument the file is cleaned in place.

set -euo pipefail

IN="${1:?usage: clean-character-audio.sh <input.mp4> [output.mp4]}"
OUT="${2:-}"
[ -f "$IN" ] || { echo "error: no such file: $IN" >&2; exit 1; }

INPLACE=0
if [ -z "$OUT" ]; then INPLACE=1; OUT="${IN%.mp4}.cleaned.tmp.mp4"; fi

VOICE_TARGET_DB=-20        # every take is normalised to this before fixed-threshold work
ANALYSIS_START=0.4         # measure over the spoken region, not the lead-in
ANALYSIS_DUR=3.0

band() {  # $1=file $2=filter
  ffmpeg -hide_banner -ss "$ANALYSIS_START" -t "$ANALYSIS_DUR" -i "$1" \
    -af "$2,volumedetect" -f null NUL 2>&1 \
    | sed -n 's/.*mean_volume: *\(-*[0-9.]*\) dB.*/\1/p' | head -1
}
evenness() {  # stddev of per-block RMS across speech: lower = steadier volume
  ffmpeg -hide_banner -ss "$ANALYSIS_START" -t "$ANALYSIS_DUR" -i "$1" \
    -af "astats=metadata=1:reset=8,ametadata=print:key=lavfi.astats.Overall.RMS_level" \
    -f null NUL 2>&1 | sed -n 's/.*RMS_level=//p' | python3 -c "
import sys, statistics
v=[float(x) for x in sys.stdin if x.strip() not in ('-inf','')]
v=[x for x in v if x > -45]
print(round(statistics.pstdev(v),2) if len(v)>2 else 0)"
}

VOX="highpass=f=300,lowpass=f=3400"
HFB="highpass=f=4000,lowpass=f=8000"
SIB="highpass=f=8000,lowpass=f=14000"

B_VOX=$(band "$IN" "$VOX"); B_HF=$(band "$IN" "$HFB"); B_SIB=$(band "$IN" "$SIB"); B_EVEN=$(evenness "$IN")
PREGAIN=$(python3 -c "print(round($VOICE_TARGET_DB - float('$B_VOX'), 2))")

CHAIN="highpass=f=85,volume=${PREGAIN}dB,afftdn=nr=10:nf=-33:tn=1,\
acompressor=threshold=-26dB:ratio=3:attack=15:release=250:makeup=4,\
agate=threshold=0.020:ratio=6:attack=8:release=400:knee=6:range=0.05,\
loudnorm=I=-19:TP=-2:LRA=7,alimiter=limit=0.94"

ffmpeg -v error -i "$IN" -af "$CHAIN" -c:v copy -c:a aac -b:a 192k -ar 48000 -movflags +faststart "$OUT" -y

A_VOX=$(band "$OUT" "$VOX"); A_HF=$(band "$OUT" "$HFB"); A_SIB=$(band "$OUT" "$SIB"); A_EVEN=$(evenness "$OUT")

python3 - "$(basename "$IN")" "$PREGAIN" "$B_VOX" "$A_VOX" "$B_HF" "$A_HF" "$B_SIB" "$A_SIB" "$B_EVEN" "$A_EVEN" <<'PY'
import sys
name, pg, bv, av, bh, ah, bs, as_, be, ae = sys.argv[1:11]
bv,av,bh,ah,bs,as_,be,ae = map(float,(bv,av,bh,ah,bs,as_,be,ae))
hf_b, hf_a = bh-bv, ah-av          # clarity = HF relative to the voice band
sb_b, sb_a = bs-bv, as_-av
print(f"  {name}")
print(f"    pre-gain        {pg:>7} dB")
print(f"    clarity HF rel  {hf_b:+7.1f} -> {hf_a:+7.1f} dB   ({hf_a-hf_b:+.1f})")
print(f"    sibilance  rel  {sb_b:+7.1f} -> {sb_a:+7.1f} dB   ({sb_a-sb_b:+.1f})")
print(f"    evenness stddev {be:7.2f} -> {ae:7.2f}      ({ae-be:+.2f}, lower is steadier)")
if hf_a - hf_b < -1.0:
    print(f"    WARNING: clarity dropped {hf_b-hf_a:.1f} dB. Lower afftdn nr for this take.")
if ae > be:
    print(f"    WARNING: levels got less even on this take.")
PY

[ "$INPLACE" = "1" ] && mv -f "$OUT" "$IN" || true
