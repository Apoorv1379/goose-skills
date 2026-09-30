"""Tests for review_finished_ad.py.

Run: python3 -m pytest tests/   (needs ffmpeg, numpy, pillow; no network)
Every video is synthesised here with ffmpeg, so each test states the defect it plants.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import review_finished_ad as rfa  # noqa: E402

pytestmark = pytest.mark.skipif(
    subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0, reason="ffmpeg not installed"
)


def _font(size):
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def make_logo(path, text, colour, shape):
    im = Image.new("RGBA", (700, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shape == "circle":
        d.ellipse([10, 40, 230, 260], fill=colour)
    else:
        d.polygon([(10, 260), (120, 40), (230, 260)], fill=colour)
    d.text((250, 70), text, font=_font(140), fill=colour)
    im.save(path)
    return path


def make_endcard(path, logo_path, size=(1080, 1920), bg=(245, 240, 230), logo_width=600, recolour=None):
    card = Image.new("RGB", size, bg)
    logo = Image.open(logo_path).convert("RGBA")
    if recolour:
        solid = Image.new("RGBA", logo.size, recolour + (255,))
        logo = Image.composite(solid, Image.new("RGBA", logo.size, (0, 0, 0, 0)), logo.split()[3])
    scale = logo_width * size[0] / 1080 / logo.width
    logo = logo.resize((round(logo.width * scale), round(logo.height * scale)))
    card.paste(logo, ((size[0] - logo.width) // 2, size[1] // 2 - logo.height), logo)
    card.save(path)
    return path


def make_video(tmp, endcard, size="1080x1920", body_s=4.5, still_body=False, silent_lead=0.0, gap=None):
    """Moving test pattern for the body, then 2.5s end card; a tone over all of it."""
    w, h = size.split("x")
    out = Path(tmp) / "v.mp4"
    body = f"color=c=0x336699:s={w}x{h}:r=30:d={body_s}" if still_body else f"testsrc2=s={w}x{h}:r=30:d={body_s}"
    total = body_s + 2.5
    vol = [f"between(t,0,{silent_lead})"] if silent_lead else []
    if gap:
        vol.append(f"between(t,{gap[0]},{gap[1]})")
    af = f"volume=enable='{'+'.join(vol)}':volume=0" if vol else "anull"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", body,
        "-loop", "1", "-t", "2.5", "-r", "30", "-i", str(endcard),
        "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=44100:duration={total}",
        "-filter_complex", f"[0:v]format=yuv420p,setsar=1[a];[1:v]scale={w}:{h},format=yuv420p,setsar=1[b];"
                           f"[a][b]concat=n=2:v=1:a=0[v];[2:a]{af}[au]",
        "-map", "[v]", "-map", "[au]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


@pytest.fixture()
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture()
def logos(tmp):
    return (make_logo(tmp / "acme.png", "ACME", (20, 60, 200, 255), "circle"),
            make_logo(tmp / "zeta.png", "ZETA", (200, 40, 40, 255), "tri"))


def run(tmp, video, *extra):
    out = tmp / "verdict.json"
    code = rfa.main(["--video", str(video), "--json", str(out), "--sheet", str(tmp / "sheet.png"), *extra])
    return code, (json.loads(out.read_text()) if out.exists() else None)


def test_clean_ad_with_the_right_logo_passes(tmp, logos):
    acme, _ = logos
    v = make_video(tmp, make_endcard(tmp / "card.png", acme))
    code, r = run(tmp, v, "--logo", str(acme), "--palette", "#1438c8,#f5f0e6")
    assert code == 0, r["checks"]
    assert r["checks"]["logo"]["status"] == "pass"
    assert r["checks"]["palette"]["status"] == "pass"
    assert (tmp / "sheet.png").exists()


def test_wrong_logo_on_end_card_fails(tmp, logos):
    acme, zeta = logos
    v = make_video(tmp, make_endcard(tmp / "card.png", zeta))
    code, r = run(tmp, v, "--logo", str(acme))
    assert code == 2
    assert r["checks"]["logo"]["status"] == "fail"


def test_smaller_recoloured_logo_is_still_found(tmp, logos):
    acme, _ = logos
    v = make_video(tmp, make_endcard(tmp / "card.png", acme, logo_width=300, recolour=(30, 30, 30)))
    code, r = run(tmp, v, "--logo", str(acme))
    assert r["checks"]["logo"]["status"] == "pass", r["checks"]["logo"]


def _photo_logo(tmp, name, text, colour, shape, bg):
    """An OPAQUE logo file (like a mascot JPEG): the mark on its own coloured square."""
    src = make_logo(tmp / f"{name}.png", text, colour, shape)
    im = Image.new("RGB", (700, 700), bg)
    mark = Image.open(src)
    im.paste(mark, (0, 200), mark)
    out = tmp / f"{name}.jpg"
    im.save(out, quality=92)
    return out


def test_opaque_photo_logo_right_passes_wrong_fails(tmp):
    right = _photo_logo(tmp, "right", "ACME", (20, 60, 200, 255), "circle", (170, 205, 140))
    wrong = _photo_logo(tmp, "wrong", "ZETA", (200, 40, 40, 255), "tri", (170, 205, 140))
    v = make_video(tmp, make_endcard(tmp / "card.png", right, logo_width=460))
    code, r = run(tmp, v, "--logo", str(right))
    assert r["checks"]["logo"]["status"] == "pass", r["checks"]["logo"]
    assert r["checks"]["logo"]["data"]["mode"] == "image"
    v = make_video(tmp, make_endcard(tmp / "card2.png", wrong, logo_width=460))
    code, r = run(tmp, v, "--logo", str(right))
    assert r["checks"]["logo"]["status"] == "fail", r["checks"]["logo"]


def test_favicon_logo_file_fails(tmp, logos):
    acme, _ = logos
    fav = tmp / "fav.png"
    Image.open(acme).resize((32, 14)).save(fav)
    v = make_video(tmp, make_endcard(tmp / "card.png", acme))
    code, r = run(tmp, v, "--logo", str(fav))
    assert code == 2
    assert r["checks"]["logo_asset"]["status"] == "fail"


def test_landscape_output_fails_ratio(tmp, logos):
    acme, _ = logos
    v = make_video(tmp, make_endcard(tmp / "card.png", acme, size=(1920, 1080)), size="1920x1080")
    code, r = run(tmp, v)
    assert code == 2
    assert r["checks"]["ratio"]["status"] == "fail"


def test_still_opening_and_frozen_body_fail_hook_and_pacing(tmp, logos):
    acme, _ = logos
    v = make_video(tmp, make_endcard(tmp / "card.png", acme), still_body=True)
    code, r = run(tmp, v)
    assert code == 2
    assert r["checks"]["hook"]["status"] == "fail"
    assert r["checks"]["pacing"]["status"] == "fail"


def test_late_sound_fails_hook(tmp, logos):
    acme, _ = logos
    v = make_video(tmp, make_endcard(tmp / "card.png", acme), silent_lead=1.8)
    code, r = run(tmp, v)
    assert r["checks"]["hook"]["status"] == "fail"


def test_mid_video_silence_fails_dead_air_unless_no_speech(tmp, logos):
    acme, _ = logos
    v = make_video(tmp, make_endcard(tmp / "card.png", acme), gap=(1.5, 3.2))
    code, r = run(tmp, v)
    assert r["checks"]["dead_air"]["status"] == "fail"
    code, r = run(tmp, v, "--no-speech")
    assert r["checks"]["dead_air"]["status"] == "not_applicable"


def test_off_palette_end_card_warns_not_fails(tmp, logos):
    acme, _ = logos
    v = make_video(tmp, make_endcard(tmp / "card.png", acme))
    code, r = run(tmp, v, "--palette", "#00ff00,#ff00ff")
    assert r["checks"]["palette"]["status"] == "warn"
    assert code == 0


def test_missing_video_is_an_error(tmp):
    assert rfa.main(["--video", str(tmp / "nope.mp4"), "--json", str(tmp / "o.json")]) == 3
