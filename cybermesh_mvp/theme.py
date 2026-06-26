"""Cybermesh cyberpunk UI theme."""

from __future__ import annotations

from typing import Tuple

from PIL import ImageDraw

APP_NAME = "CYBERMESH"
APP_TAGLINE = "mesh // uplink"

# Core palette (RGBA)
COL_BG = (6, 4, 14, 255)
COL_BG_LINE = (14, 8, 28, 255)
COL_PANEL = (16, 8, 28, 255)
COL_PANEL_EDGE = (42, 14, 62, 255)
COL_ACCENT = (0, 245, 255, 255)
COL_ACCENT2 = (255, 0, 170, 255)
COL_TEXT = (225, 248, 255, 255)
COL_DIM = (110, 95, 145, 255)
COL_SEL = (50, 0, 75, 255)
COL_SEL_EDGE = (255, 0, 170, 255)
COL_ERR = (255, 45, 90, 255)
COL_ME = (0, 230, 255, 255)
COL_HI = (70, 10, 95, 255)
COL_WARN = (255, 200, 0, 255)

MAP_THEMES = {
    "light": {
        "bg": (22, 16, 36, 255),
        "grid": (50, 30, 70, 255),
        "text": (0, 220, 240, 255),
        "dim": (120, 100, 150, 255),
        "me": (0, 255, 255, 255),
        "node": (255, 180, 0, 255),
        "sel": (255, 0, 170, 255),
        "line": (255, 0, 170, 220),
    },
    "dark": {
        "bg": (6, 4, 14, 255),
        "grid": (28, 16, 48, 255),
        "text": (0, 245, 255, 255),
        "dim": (110, 95, 145, 255),
        "me": (0, 255, 255, 255),
        "node": (255, 210, 40, 255),
        "sel": (255, 0, 170, 255),
        "line": (255, 0, 170, 200),
    },
}


def header_title(state: str) -> str:
    title = APP_NAME
    if state == "connected":
        title += "  //  LINK"
    elif state == "connecting":
        title += "  //  SYNC"
    elif state == "scanning":
        title += "  //  SCAN"
    return title


def draw_background(d: ImageDraw.ImageDraw, w: int, h: int) -> None:
    d.rectangle([0, 0, w, h], fill=COL_BG)
    for x in range(0, w, 40):
        d.line([(x, 0), (x, h)], fill=COL_BG_LINE, width=1)
    for y in range(0, h, 4):
        d.line([(0, y), (w, y)], fill=(10, 6, 20, 255), width=1)


def draw_header(
    d: ImageDraw.ImageDraw, w: int, header_h: int, fonts, title: str
) -> None:
    d.rectangle([0, 0, w, header_h], fill=COL_PANEL)
    d.line([(0, header_h - 1), (w, header_h - 1)], fill=COL_ACCENT, width=1)
    d.line([(0, header_h - 3), (w // 3, header_h - 3)], fill=COL_ACCENT2, width=1)
    bracket = 8
    d.line([(4, 4), (4 + bracket, 4)], fill=COL_ACCENT, width=2)
    d.line([(4, 4), (4, 4 + bracket)], fill=COL_ACCENT, width=2)
    d.line([(w - 5, 4), (w - 5 - bracket, 4)], fill=COL_ACCENT2, width=2)
    d.line([(w - 5, 4), (w - 5, 4 + bracket)], fill=COL_ACCENT2, width=2)
    fonts.draw(d, (14, 4), title, COL_ACCENT, "large")
    fonts.draw(d, (w - 120, 10), APP_TAGLINE, COL_ACCENT2, "small")


def draw_footer_bar(
    d: ImageDraw.ImageDraw, w: int, h: int, footer_h: int, fonts, text: str
) -> None:
    y = h - footer_h
    d.rectangle([0, y, w, h], fill=COL_PANEL)
    d.line([(0, y), (w, y)], fill=COL_ACCENT2, width=1)
    d.line([(w * 2 // 3, y + 2), (w, y + 2)], fill=COL_ACCENT, width=1)
    fonts.draw(d, (8, y + 6), text, COL_DIM, "small")


def draw_list_item(d: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], selected: bool) -> None:
    x0, y0, x1, y1 = box
    if selected:
        d.rectangle(box, fill=COL_SEL, outline=COL_SEL_EDGE, width=1)
        tick = 4
        d.line([(x0, y0), (x0 + tick, y0)], fill=COL_ACCENT, width=2)
        d.line([(x0, y0), (x0, y0 + tick)], fill=COL_ACCENT, width=2)
        d.line([(x1, y1), (x1 - tick, y1)], fill=COL_ACCENT2, width=2)
        d.line([(x1, y1), (x1, y1 - tick)], fill=COL_ACCENT2, width=2)
    else:
        d.rectangle(box, fill=COL_PANEL, outline=COL_PANEL_EDGE, width=1)


def draw_panel_box(
    d: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], *, accent: bool = False
) -> None:
    edge = COL_ACCENT if accent else COL_PANEL_EDGE
    d.rectangle(box, fill=COL_PANEL, outline=edge, width=1)


def draw_menu_frame(d: ImageDraw.ImageDraw, overlay: Tuple[int, int, int, int]) -> None:
    d.rectangle(overlay, fill=COL_PANEL, outline=COL_ACCENT, width=2)
    x0, y0, x1, y1 = overlay
    tick = 6
    for ox, oy, dx, dy in (
        (x0, y0, 1, 1),
        (x1, y0, -1, 1),
        (x0, y1, 1, -1),
        (x1, y1, -1, -1),
    ):
        d.line([(ox, oy), (ox + dx * tick, oy)], fill=COL_ACCENT2, width=2)
        d.line([(ox, oy), (ox, oy + dy * tick)], fill=COL_ACCENT2, width=2)
