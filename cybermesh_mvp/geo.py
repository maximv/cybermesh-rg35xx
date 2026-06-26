"""Geodesy helpers and OSM tile fetch/cache."""

from __future__ import annotations

import math
import threading
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

TILE_SIZE = 256
OSM_UA = "CybermeshRG35xx/1.0 (handheld)"
TILE_URLS = {
    "light": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "dark": "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
}
_TILE_DL_LOCK = threading.Lock()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def format_distance(m: float) -> str:
    if m < 1000:
        return f"{int(m)}m"
    return f"{m / 1000:.1f}km"


def deg2tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def mercator_pixel(lon: float, lat: float, zoom: int) -> Tuple[float, float]:
    """World pixel coords at zoom level (Web Mercator, OSM slippy map)."""
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n * TILE_SIZE
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n * TILE_SIZE
    return x, y


def latlon_to_pixel(lat: float, lon: float, center_lat: float, center_lon: float,
                    zoom: int, width: int, height: int,
                    pan_x: int = 0, pan_y: int = 0) -> Tuple[int, int]:
    """Map lat/lon to screen pixel; center_lat/lon is at the map centre."""
    wx, wy = mercator_pixel(lon, lat, zoom)
    cx, cy = mercator_pixel(center_lon, center_lat, zoom)
    sx = int(width / 2 + (wx - cx) + pan_x)
    sy = int(height / 2 + (wy - cy) + pan_y)
    return sx, sy


def tile2deg(x: int, y: int, zoom: int) -> Tuple[float, float]:
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad), lon


def tile_cache_path(cache_dir: Path, theme: str, z: int, x: int, y: int) -> Path:
    return cache_dir / theme / str(z) / str(x) / f"{y}.png"


def read_tile_cache(cache_dir: Path, theme: str, z: int, x: int, y: int) -> Optional[bytes]:
    """Read a cached tile only (never blocks on network)."""
    path = tile_cache_path(cache_dir, theme, z, x, y)
    if path.exists() and path.stat().st_size > 100:
        return path.read_bytes()
    return None


def download_tile(
    cache_dir: Path,
    theme: str,
    z: int,
    x: int,
    y: int,
    timeout: float = 3.0,
) -> Optional[bytes]:
    """Download one tile and cache it."""
    cached = read_tile_cache(cache_dir, theme, z, x, y)
    if cached is not None:
        return cached
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = tile_cache_path(cache_dir, theme, z, x, y)
    url = TILE_URLS.get(theme, TILE_URLS["light"]).format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": OSM_UA})
    try:
        with _TILE_DL_LOCK:
            cached = read_tile_cache(cache_dir, theme, z, x, y)
            if cached is not None:
                return cached
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data
    except Exception:  # noqa: BLE001
        return read_tile_cache(cache_dir, theme, z, x, y)


def fetch_tile(
    cache_dir: Path,
    theme: str,
    z: int,
    x: int,
    y: int,
    timeout: float = 3.0,
) -> Optional[bytes]:
    """Cache-first tile load; may download (prefer read_tile_cache on UI thread)."""
    return download_tile(cache_dir, theme, z, x, y, timeout=timeout)


def metres_per_pixel(lat: float, zoom: int) -> float:
    """Approximate ground metres per screen pixel at zoom level."""
    return 156543.03 * math.cos(math.radians(lat)) / (2 ** zoom)

