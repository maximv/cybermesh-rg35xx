"""Hybrid OSM / schematic map renderer for node positions."""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from .geo import (
    TILE_SIZE,
    deg2tile,
    download_tile,
    format_distance,
    latlon_to_pixel,
    mercator_pixel,
    metres_per_pixel,
    read_tile_cache,
)
from .radio import NodeInfo
from .theme import MAP_THEMES

THEMES = MAP_THEMES


def _node_label(n: NodeInfo, max_len: int = 36) -> str:
    short = (n.short or "?").strip()
    long_name = (n.long or short).strip()
    if long_name.lower() == short.lower():
        return short[:max_len]
    return f"{short} {long_name}"[:max_len]


def _me_label(short: str, long_name: str, max_len: int = 36) -> str:
    short = (short or "Я").strip()
    long_name = (long_name or short).strip()
    if long_name.lower() == short.lower():
        return f"Я {short}"[:max_len]
    return f"Я {short} {long_name}"[:max_len]


class MapView:
    def __init__(self, cache_dir: Path, width: int = 640, height: int = 48) -> None:
        self.cache_dir = cache_dir
        self.W = width
        self.H = height
        self.map_h = height - 48  # leave room for header/footer in fbui
        self.center_lat = 0.0
        self.center_lon = 0.0
        self.zoom = 14
        self.pan_x = 0
        self.pan_y = 0
        self.use_tiles = True
        self.theme = "dark"
        self._prefetch_gen = 0
        self._anim_active = False
        self._anim_from: Tuple[float, float] = (0.0, 0.0)
        self._anim_to: Tuple[float, float] = (0.0, 0.0)
        self._anim_t0 = 0.0
        self._anim_dur = 0.32

    def _pal(self) -> Dict[str, Tuple[int, ...]]:
        return THEMES.get(self.theme, THEMES["light"])

    def toggle_theme(self) -> str:
        self.theme = "dark" if self.theme == "light" else "light"
        self._kick_prefetch()
        return self.theme

    def center_on(self, lat: float, lon: float) -> None:
        self._anim_active = False
        self.center_lat = lat
        self.center_lon = lon
        self.pan_x = 0
        self.pan_y = 0
        self._kick_prefetch()

    def animate_to(self, lat: float, lon: float) -> None:
        """Smoothly slide the map centre to (lat, lon) so movement direction reads."""
        if self.center_lat == 0.0 and self.center_lon == 0.0:
            self.center_on(lat, lon)
            return
        self._anim_from = (self.center_lat + 0.0, self.center_lon + 0.0)
        self._anim_to = (lat, lon)
        self._anim_t0 = time.monotonic()
        self.pan_x = 0
        self.pan_y = 0
        self._anim_active = True

    def update_anim(self) -> bool:
        """Advance an in-progress pan animation. Returns True if the centre moved."""
        if not self._anim_active:
            return False
        p = (time.monotonic() - self._anim_t0) / max(0.01, self._anim_dur)
        if p >= 1.0:
            self.center_lat, self.center_lon = self._anim_to
            self._anim_active = False
            self._kick_prefetch()
            return True
        e = p * p * (3.0 - 2.0 * p)  # smoothstep
        flat, flon = self._anim_from
        tlat, tlon = self._anim_to
        self.center_lat = flat + (tlat - flat) * e
        self.center_lon = flon + (tlon - flon) * e
        return True

    def pan(self, dx: int, dy: int) -> None:
        self._anim_active = False
        self.pan_x += dx
        self.pan_y += dy

    def zoom_delta(self, delta: int) -> None:
        self.zoom = max(10, min(17, self.zoom + delta))
        self._kick_prefetch()

    def _kick_prefetch(self) -> None:
        if not self.use_tiles or self.center_lat == 0 and self.center_lon == 0:
            return
        self._prefetch_gen += 1
        gen = self._prefetch_gen
        z = self.zoom
        lat, lon = self.center_lat, self.center_lon
        w, h = self.W, self.map_h
        cache = self.cache_dir
        theme = self.theme

        def _work() -> None:
            tx, ty = deg2tile(lat, lon, z)
            tiles_x = w // TILE_SIZE + 2
            tiles_y = h // TILE_SIZE + 2
            for dy in range(-tiles_y // 2, tiles_y // 2 + 1):
                for dx in range(-tiles_x // 2, tiles_x // 2 + 1):
                    if gen != self._prefetch_gen:
                        return
                    download_tile(cache, theme, z, tx + dx, ty + dy, timeout=4.0)

        threading.Thread(target=_work, daemon=True).start()

    def render(
        self,
        nodes: List[NodeInfo],
        my_lat: Optional[float],
        my_lon: Optional[float],
        fonts,
        my_num: Optional[int] = None,
        nodes_loading: bool = False,
        selected: Optional[NodeInfo] = None,
        my_short: str = "Я",
        my_long: str = "",
    ) -> Image.Image:
        pal = self._pal()
        img = Image.new("RGBA", (self.W, self.map_h), pal["bg"])
        positioned = [n for n in nodes if n.lat is not None and n.lon is not None]
        have_me = my_lat is not None and my_lon is not None
        if have_me:
            ref_lat, ref_lon = my_lat, my_lon
        elif positioned:
            ref_lat = sum(n.lat for n in positioned) / len(positioned)
            ref_lon = sum(n.lon for n in positioned) / len(positioned)
        else:
            d = ImageDraw.Draw(img)
            if nodes_loading:
                fonts.draw(d, (12, self.map_h // 2 - 24), "Загрузка узлов", pal["text"], "normal")
                fonts.draw(d, (12, self.map_h // 2 + 4), "ждите позиции с эфира", pal["dim"], "small")
            else:
                fonts.draw(d, (12, self.map_h // 2 - 24), "Нет позиции", pal["text"], "normal")
                fonts.draw(d, (12, self.map_h // 2 + 4), "нет GPS и узлов на карте", pal["dim"], "small")
            return img

        if self.center_lat == 0 and self.center_lon == 0:
            self.center_on(ref_lat, ref_lon)

        has_tiles = False
        if self.use_tiles:
            has_tiles = self._draw_tiles_cached(img)
        if not has_tiles:
            self._draw_schematic_grid(img, ref_lat, pal)

        self._draw_markers(
            img, nodes, fonts, pal, my_num=my_num,
            show_me=have_me, me_lat=my_lat, me_lon=my_lon, selected=selected,
            my_short=my_short, my_long=my_long,
        )
        return img

    def _screen_xy(self, lat: float, lon: float) -> Tuple[int, int]:
        return latlon_to_pixel(
            lat, lon, self.center_lat, self.center_lon,
            self.zoom, self.W, self.map_h, self.pan_x, self.pan_y,
        )

    def _tile_origin(self) -> Tuple[int, int, int, int]:
        z = self.zoom
        cx, cy = mercator_pixel(self.center_lon, self.center_lat, z)
        tx, ty = deg2tile(self.center_lat, self.center_lon, z)
        tile_ox = tx * TILE_SIZE
        tile_oy = ty * TILE_SIZE
        paste_x = self.W // 2 + self.pan_x - int(cx - tile_ox)
        paste_y = self.map_h // 2 + self.pan_y - int(cy - tile_oy)
        return tx, ty, paste_x, paste_y

    def _draw_tiles_cached(self, img: Image.Image) -> bool:
        z = self.zoom
        tx, ty, paste_x, paste_y = self._tile_origin()
        tiles_x = self.W // TILE_SIZE + 2
        tiles_y = self.map_h // TILE_SIZE + 2
        got_any = False
        for dy in range(-tiles_y // 2, tiles_y // 2 + 1):
            for dx in range(-tiles_x // 2, tiles_x // 2 + 1):
                x, y = tx + dx, ty + dy
                data = read_tile_cache(self.cache_dir, self.theme, z, x, y)
                if not data:
                    continue
                try:
                    tile = Image.open(io.BytesIO(data)).convert("RGBA")
                    px = paste_x + dx * TILE_SIZE
                    py = paste_y + dy * TILE_SIZE
                    img.paste(tile, (px, py))
                    got_any = True
                except Exception:  # noqa: BLE001
                    pass
        if not got_any:
            self._kick_prefetch()
        return got_any

    def _draw_schematic_grid(self, img: Image.Image, ref_lat: float, pal: Dict) -> None:
        d = ImageDraw.Draw(img)
        mpp = max(1.0, metres_per_pixel(ref_lat, self.zoom))
        step_m = 500.0
        if mpp > 200:
            step_m = 5000.0
        elif mpp > 50:
            step_m = 1000.0
        step_px = max(24, int(step_m / mpp))
        for x in range(0, self.W, step_px):
            d.line([(x, 0), (x, self.map_h)], fill=pal["grid"], width=1)
        for y in range(0, self.map_h, step_px):
            d.line([(0, y), (self.W, y)], fill=pal["grid"], width=1)

    def _draw_markers(
        self,
        img: Image.Image,
        nodes: List[NodeInfo],
        fonts,
        pal: Dict,
        my_num: Optional[int] = None,
        *,
        show_me: bool = False,
        me_lat: Optional[float] = None,
        me_lon: Optional[float] = None,
        selected: Optional[NodeInfo] = None,
        my_short: str = "Я",
        my_long: str = "",
    ) -> None:
        d = ImageDraw.Draw(img)
        me_xy: Optional[Tuple[int, int]] = None
        if show_me and me_lat is not None and me_lon is not None:
            me_xy = self._screen_xy(me_lat, me_lon)
            mx, my = me_xy
            d.ellipse([mx - 6, my - 6, mx + 6, my + 6], fill=pal["me"])
            fonts.draw(d, (mx + 10, my - 8), _me_label(my_short, my_long), pal["text"], "small")

        if selected and selected.lat is not None and selected.lon is not None:
            sx, sy = self._screen_xy(selected.lat, selected.lon)
            ox, oy = me_xy if me_xy else (self.W // 2 + self.pan_x, self.map_h // 2 + self.pan_y)
            d.line([(ox, oy), (sx, sy)], fill=pal["line"], width=2)

        for n in nodes:
            if my_num is not None and n.num == my_num:
                continue
            if n.lat is None or n.lon is None:
                continue
            px, py = self._screen_xy(n.lat, n.lon)
            is_sel = selected is not None and n.num == selected.num
            r = 7 if is_sel else 4
            col = pal["sel"] if is_sel else pal["node"]
            d.ellipse([px - r, py - r, px + r, py + r], fill=col)
            if is_sel:
                label = _node_label(n)
                if n.distance_m is not None:
                    label += f" {format_distance(n.distance_m)}"
                fonts.draw(d, (px + 8, py - 8), label, pal["text"], "small")
