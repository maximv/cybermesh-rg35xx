"""Cyberpunk radar unfold/fold splash animation."""

from __future__ import annotations

import math
import random
import time
from typing import Any, Callable, Optional

from PIL import Image, ImageDraw

from .theme import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    COL_ACCENT,
    COL_ACCENT2,
    COL_BG,
    COL_DIM,
    COL_PANEL,
    COL_TEXT,
    draw_background,
)


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _glitch_image(img: Image.Image, amount: float, seed: int) -> Image.Image:
    if amount < 0.06:
        return img
    rng = random.Random(seed)
    out = img.copy()
    w, h = out.size
    for _ in range(int(2 + amount * 14)):
        y0 = rng.randrange(0, max(1, h - 8))
        bh = rng.randint(2, max(3, int(14 * amount)))
        y1 = min(h, y0 + bh)
        strip = img.crop((0, y0, w, y1))
        dx = rng.randint(-int(22 * amount), int(22 * amount))
        out.paste(strip, (dx, y0))
    return out


def _scanlines(d: ImageDraw.ImageDraw, w: int, h: int, alpha: int = 28) -> None:
    for y in range(0, h, 3):
        d.line([(0, y), (w, y)], fill=(0, 0, 0, alpha), width=1)


def draw_radar_frame(
    d: ImageDraw.ImageDraw,
    w: int,
    h: int,
    t: float,
    *,
    unfold: bool,
    fonts: Any,
    phase_text: str = "",
) -> float:
    """Draw one splash frame. Returns eased openness 0..1."""
    if unfold:
        ease = _smoothstep(t)
        glitch = max(0.0, 1.0 - ease * 1.35)
    else:
        ease = _smoothstep(1.0 - t)
        glitch = max(0.0, 1.0 - ease * 1.1)

    cx, cy = w // 2, h // 2 - 18
    max_r = min(w, h) // 2 - 58

    # HUD corner brackets slide with radar openness.
    inset = int(24 + (1.0 - ease) * 46)
    tick = int(10 + ease * 8)
    for x0, y0, dx, dy in (
        (inset, inset, 1, 1),
        (w - inset, inset, -1, 1),
        (inset, h - inset, 1, -1),
        (w - inset, h - inset, -1, -1),
    ):
        d.line([(x0, y0), (x0 + dx * tick, y0)], fill=COL_ACCENT, width=2)
        d.line([(x0, y0), (x0, y0 + dy * tick)], fill=COL_ACCENT, width=2)

    # Concentric rings grow outward.
    rings = 4
    for i in range(1, rings + 1):
        ring_ease = max(0.0, min(1.0, (ease - (i - 1) * 0.14) / 0.72))
        if ring_ease <= 0.01:
            continue
        r = int(max_r * ring_ease * (i / rings))
        if r < 4:
            continue
        color = COL_ACCENT if i % 2 else COL_DIM
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=1)

    # Radar dish "unfolds" from a vertical slit into a full sweep arc.
    span = max(4.0, ease * 360.0)
    r_main = int(max_r * max(0.15, ease))
    if r_main >= 6:
        start = -90.0 - span / 2.0
        d.arc(
            [cx - r_main, cy - r_main, cx + r_main, cy + r_main],
            start,
            start + span,
            fill=COL_ACCENT,
            width=2,
        )

    # Rotating sweep beam.
    if ease > 0.12:
        sweep = math.radians((t * 720.0 if unfold else (1.0 - t) * 720.0) - 90.0)
        beam_r = int(max_r * ease)
        ex = cx + int(math.cos(sweep) * beam_r)
        ey = cy + int(math.sin(sweep) * beam_r)
        d.line([(cx, cy), (ex, ey)], fill=COL_ACCENT2, width=2)
        # faint trail wedge
        trail = math.radians(18)
        tx = cx + int(math.cos(sweep - trail) * beam_r * 0.85)
        ty = cy + int(math.sin(sweep - trail) * beam_r * 0.85)
        d.line([(cx, cy), (tx, ty)], fill=(255, 0, 170, 90), width=1)

    # Crosshair arms extend with the dish.
    arm = int(max_r * ease * 0.92)
    if arm > 2:
        d.line([(cx - arm, cy), (cx + arm, cy)], fill=COL_ACCENT2, width=1)
        d.line([(cx, cy - arm), (cx, cy + arm)], fill=COL_ACCENT2, width=1)

    # Center blip
    if ease > 0.25:
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=COL_ACCENT)

    # Collapse / unfold slit (vertical lines converging when folding).
    slit = int((1.0 - ease) * max_r * 0.55)
    if slit > 2:
        d.line([(cx, cy - slit), (cx, cy + slit)], fill=COL_ACCENT2, width=2)

    _scanlines(d, w, h, alpha=int(18 + glitch * 30))

    # Title fades in as radar opens.
    if ease > 0.35 and fonts is not None:
        title_y = cy + max_r + 22
        fonts.draw(d, (w // 2 - fonts.length(APP_NAME, "large") // 2, title_y), APP_NAME, COL_ACCENT, "large")
        tag = f"{APP_TAGLINE}  ·  v{APP_VERSION}"
        fonts.draw(
            d,
            (w // 2 - fonts.length(tag, "small") // 2, title_y + 28),
            tag,
            COL_ACCENT2,
            "small",
        )

    if phase_text and fonts is not None and ease > 0.2:
        # Top of the screen, clear of the CYBERMESH title/tagline at the bottom.
        phase_y = max(14, cy - max_r - 24)
        fonts.draw(
            d,
            (w // 2 - fonts.length(phase_text, "small") // 2, phase_y),
            phase_text,
            COL_DIM,
            "small",
        )

    return ease


def play_radar_splash(
    screen: Any,
    fonts: Any,
    *,
    unfold: bool,
    duration: float = 1.7,
    fps: float = 28.0,
    phase_text: str = "",
    pump: Optional[Callable[[], None]] = None,
) -> None:
    w, h = screen.width, screen.height
    frame_dt = 1.0 / max(1.0, fps)
    frames = max(2, int(duration * fps))
    raw_mode = getattr(screen, "raw_mode", "BGRA")

    for i in range(frames):
        t = i / (frames - 1)
        img = Image.new("RGBA", (w, h), COL_BG)
        d = ImageDraw.Draw(img)
        draw_background(d, w, h)

        d.rectangle([8, 8, w - 8, h - 8], outline=COL_PANEL, width=1)
        ease = draw_radar_frame(
            d, w, h, t, unfold=unfold, fonts=fonts, phase_text=phase_text
        )

        glitch = max(0.0, 1.0 - ease) if unfold else max(0.0, 1.0 - ease * 0.85)
        if glitch > 0.05:
            img = _glitch_image(img, glitch, seed=i * 9973 + (1 if unfold else 7))

        screen.present(img.tobytes("raw", raw_mode))
        if pump is not None:
            pump()
        # Less delay near the end so UI feels snappy.
        delay = frame_dt * (0.65 + 0.35 * (1.0 - abs(0.5 - t) * 2.0))
        time.sleep(delay)
