# Smoke test — render-powerful-skills-episode

Free. Nothing here makes a paid call.

## 1 · The free half of a run stops before spending

```bash
python scripts/new_episode.py --series <series>/
```

Expect: a skill picked off the live site, a demo transcript, five written lines in
`episode.json` including `vo_line_c`, a captured `screen-recording/zoom-edit.mp4`, a drafted
payoff spec in `working/`, an itemised bill, and the run stopping with
`STOPPING before the paid calls`.

**Fails if** it reaches `State 4 - hook wave submitted` without `--yes`.

## 2 · The demo is long enough to carry the voice

```bash
python scripts/check-demo-length.py <ep>/screen-recording/zoom-edit.mp4 "candidate"
```

Expect print points continuing past the pan. A capture that stops printing a few seconds in
leaves the middle on a frozen frame, and no capture setting fixes it — change the featured
skill instead.

**Read the print points, not the verdict line.** The thresholds are not calibrated: measured
unretimed they pass a capture that was rejected in production.

## 3 · The payoff renders without a voice

```bash
python scripts/make-board-broll.py --spec <ep>/working/board-spec.json \
    --still <ep>/review/broll-still.png --still-only --out /dev/null
```

Expect a 1080x1920 PNG inside the 4:5 safe zone (y 285..1634), on the terminal's palette,
with every value traceable to the demo transcript.

## 4 · The gates run, and all twelve report

```bash
python scripts/verify-episode.py --episode "$(pwd)/<ep>" --final <ep>/output/<file>.mp4
```

**Pass an ABSOLUTE `--episode`.** A relative one resolves the locked voice through an empty
parent, and the three voice gates skip in silence while the run still prints
`all checks passed`.

Expect twelve `ok` lines. Count them.
