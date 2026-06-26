"""Fonts with per-glyph emoji fallback for small handheld screens.

Main text is drawn with a proportional TTF (DejaVuSans has Cyrillic + arrows +
geometric shapes). Emoji code points are drawn from a separate monochrome
emoji TTF (assets/emoji.ttf, e.g. NotoEmoji-Regular) using the text colour, so
they are visible on the dark UI. Without an emoji font, emoji are skipped.
"""

from __future__ import annotations

import glob
import os
from typing import Dict, List, Optional, Tuple

from PIL import ImageFont

_SEARCH_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
]

# Proportional text fonts with broad coverage (Cyrillic, symbols).
_PREFER_TEXT = ("dejavusans", "notosans", "liberationsans", "freesans", "roboto")

_SIZES = {"small": 15, "normal": 18, "large": 24}

# Codepoints to drop entirely (combine/format chars that render as tofu).
_DROP = {0xFE0F, 0x200D, 0x20E3}


def _all_ttfs() -> List[str]:
    out: List[str] = []
    for base in _SEARCH_DIRS:
        if not os.path.isdir(base):
            continue
        for ext in ("ttf", "otf"):
            out.extend(glob.glob(os.path.join(base, "**", f"*.{ext}"), recursive=True))
    return out


def _find_text_font() -> Optional[str]:
    env = os.environ.get("CYBERMESH_FONT") or os.environ.get("MESHTASTIC_FONT")
    if env and os.path.exists(env):
        return env
    bundled = os.path.join(os.path.dirname(__file__), "..", "assets", "font.ttf")
    if os.path.exists(bundled):
        return bundled
    ttfs = _all_ttfs()
    for pref in _PREFER_TEXT:
        for path in ttfs:
            name = os.path.basename(path).lower()
            if pref in name and "mono" not in name and "bold" not in name and "italic" not in name:
                return path
    # last resort: any DejaVu (incl. mono) then anything
    for path in ttfs:
        if "dejavu" in os.path.basename(path).lower():
            return path
    return ttfs[0] if ttfs else None


def _find_emoji_font() -> Optional[str]:
    env = os.environ.get("MESHTASTIC_EMOJI_FONT")
    if env and os.path.exists(env):
        return env
    bundled = os.path.join(os.path.dirname(__file__), "..", "assets", "emoji.ttf")
    if os.path.exists(bundled):
        return bundled
    for path in _all_ttfs():
        name = os.path.basename(path).lower()
        if "emoji" in name or "symbola" in name:
            return path
    return None


def _is_emoji(cp: int) -> bool:
    return (
        0x1F000 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x26FF
        or 0x2700 <= cp <= 0x27BF
        or 0x2B00 <= cp <= 0x2BFF
        or 0x1F1E6 <= cp <= 0x1F1FF
        or 0xFE00 <= cp <= 0xFE0F
    )


class Fonts:
    def __init__(self, log=print) -> None:
        text_path = _find_text_font()
        emoji_path = _find_emoji_font()
        self.text_path = text_path
        self.emoji_path = emoji_path

        self.text: Dict[str, ImageFont.FreeTypeFont] = {}
        self.emoji: Dict[str, ImageFont.FreeTypeFont] = {}

        if text_path:
            log(f"text font: {text_path}")
            for key, size in _SIZES.items():
                self.text[key] = ImageFont.truetype(text_path, size)
        else:
            log("text font: PIL default")
            for key, size in _SIZES.items():
                try:
                    self.text[key] = ImageFont.load_default(size)
                except TypeError:
                    self.text[key] = ImageFont.load_default()

        if emoji_path:
            log(f"emoji font: {emoji_path}")
            for key, size in _SIZES.items():
                try:
                    self.emoji[key] = ImageFont.truetype(emoji_path, size)
                except Exception as exc:  # noqa: BLE001
                    log(f"emoji font load failed: {exc}")
                    self.emoji = {}
                    break
        else:
            log("emoji font: none (emoji will be skipped)")

    # Backwards-compatible attribute access (.small/.normal/.large)
    @property
    def small(self):
        return self.text["small"]

    @property
    def normal(self):
        return self.text["normal"]

    @property
    def large(self):
        return self.text["large"]

    def _clean(self, s: str) -> str:
        return "".join(ch for ch in s if ord(ch) not in _DROP)

    def _font_for(self, ch: str, size: str):
        cp = ord(ch)
        if self.emoji and _is_emoji(cp):
            return self.emoji[size]
        return self.text[size]

    def _runs(self, s: str, size: str) -> List[Tuple[ImageFont.FreeTypeFont, str]]:
        runs: List[Tuple[ImageFont.FreeTypeFont, str]] = []
        cur = ""
        cur_font = None
        for ch in self._clean(s):
            f = self._font_for(ch, size)
            if f is cur_font:
                cur += ch
            else:
                if cur:
                    runs.append((cur_font, cur))
                cur = ch
                cur_font = f
        if cur:
            runs.append((cur_font, cur))
        return runs

    def draw(self, d, xy: Tuple[int, int], s: str, fill, size: str = "normal") -> None:
        x, y = xy
        for font, run in self._runs(s, size):
            d.text((x, y), run, font=font, fill=fill)
            x += font.getlength(run)

    def length(self, s: str, size: str = "small") -> float:
        return sum(font.getlength(run) for font, run in self._runs(s, size))
