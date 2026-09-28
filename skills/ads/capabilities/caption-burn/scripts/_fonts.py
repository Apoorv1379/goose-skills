"""Font discovery for caption rendering: a bold sans TTF on any OS. No paid calls."""
import os
import pathlib
import urllib.request

FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
    # Windows
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\seguibl.ttf",
]
# Last resort: a free bold sans fetched once into the user cache.
FONT_URL = "https://github.com/google/fonts/raw/main/apache/roboto/static/Roboto-Bold.ttf"
FONT_CACHE = pathlib.Path(os.path.expanduser("~/.cache/gooseworks/fonts/Roboto-Bold.ttf"))


def font_path(override=None):
    """A bold sans TTF path that exists on this machine (downloads one if none does)."""
    if override and pathlib.Path(override).exists():
        return str(override)
    env = os.environ.get("GW_CAPTION_FONT")
    if env and pathlib.Path(env).exists():
        return env
    for f in FONT_CANDIDATES:
        if pathlib.Path(f).exists():
            return f
    if not FONT_CACHE.exists():
        FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(FONT_URL, FONT_CACHE)
    return str(FONT_CACHE)


def font(px, override=None):
    from PIL import ImageFont
    return ImageFont.truetype(font_path(override), px)
