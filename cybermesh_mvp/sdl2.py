"""Minimal ctypes binding to the SYSTEM SDL2 (2.0.12, 'mali' driver).

We deliberately do NOT use pygame: its bundled SDL has no working video
driver on this device. The stock firmware ships /usr/lib/libSDL2 with a
custom 'mali' EGL/GLES driver that owns the panel when launched from the
PORTS menu. We render the whole UI with Pillow and push RGBA frames to an
SDL streaming texture.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, byref, c_int, c_uint32, c_void_p, c_char_p
from typing import Callable, List, Optional, Tuple

SDL_INIT_VIDEO = 0x00000020
SDL_WINDOWPOS_UNDEFINED = 0x1FFF0000

SDL_WINDOW_FULLSCREEN = 0x00000001
SDL_WINDOW_OPENGL = 0x00000002
SDL_WINDOW_SHOWN = 0x00000004
SDL_WINDOW_FULLSCREEN_DESKTOP = 0x00001001

SDL_RENDERER_SOFTWARE = 0x00000001
SDL_RENDERER_ACCELERATED = 0x00000002
SDL_RENDERER_PRESENTVSYNC = 0x00000004

SDL_PIXELFORMAT_ARGB8888 = 0x16362004
SDL_PIXELFORMAT_ABGR8888 = 0x16762004
SDL_TEXTUREACCESS_STREAMING = 1

_LIB_CANDIDATES = [
    "/usr/lib/libSDL2-2.0.so.0",
    "/usr/lib/aarch64-linux-gnu/libSDL2-2.0.so.0",
    "libSDL2-2.0.so.0",
]


def _load_sdl() -> ctypes.CDLL:
    last = None
    for path in _LIB_CANDIDATES:
        try:
            return ctypes.CDLL(path)
        except OSError as exc:
            last = exc
    raise RuntimeError(f"Cannot load system SDL2: {last}")


class SdlScreen:
    """Owns an SDL window/renderer/texture and presents RGBA frames."""

    def __init__(self, width: int, height: int, log: Callable[[str], None] = print) -> None:
        self.log = log
        os.environ.setdefault("SDL_VIDEODRIVER", "mali")
        self.S = _load_sdl()
        self._bind()

        if self.S.SDL_Init(SDL_INIT_VIDEO) != 0:
            raise RuntimeError(f"SDL_Init failed: {self._err()}")

        self.window = self._create_window(width, height)
        self.renderer = self._create_renderer()

        w, h = c_int(0), c_int(0)
        if self.S.SDL_GetRendererOutputSize(self.renderer, byref(w), byref(h)) != 0 or not w.value:
            w.value, h.value = width, height
        self.width, self.height = w.value, h.value
        self.log(f"SDL output size {self.width}x{self.height} (driver={self._driver()})")

        # Try ARGB first; caller can flip raw mode if colors look wrong.
        self.pixel_format = SDL_PIXELFORMAT_ARGB8888
        self.raw_mode = "BGRA"  # Pillow raw order matching ARGB8888 little-endian
        self.texture = self.S.SDL_CreateTexture(
            self.renderer, self.pixel_format, SDL_TEXTUREACCESS_STREAMING,
            self.width, self.height,
        )
        if not self.texture:
            raise RuntimeError(f"SDL_CreateTexture failed: {self._err()}")
        self._pitch = self.width * 4

    def _bind(self) -> None:
        S = self.S
        S.SDL_Init.argtypes = [c_uint32]
        S.SDL_Init.restype = c_int
        S.SDL_GetError.restype = c_char_p
        S.SDL_GetCurrentVideoDriver.restype = c_char_p
        S.SDL_CreateWindow.argtypes = [c_char_p, c_int, c_int, c_int, c_int, c_uint32]
        S.SDL_CreateWindow.restype = c_void_p
        S.SDL_CreateRenderer.argtypes = [c_void_p, c_int, c_uint32]
        S.SDL_CreateRenderer.restype = c_void_p
        S.SDL_GetRendererOutputSize.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
        S.SDL_GetRendererOutputSize.restype = c_int
        S.SDL_CreateTexture.argtypes = [c_void_p, c_uint32, c_int, c_int, c_int]
        S.SDL_CreateTexture.restype = c_void_p
        S.SDL_UpdateTexture.argtypes = [c_void_p, c_void_p, c_void_p, c_int]
        S.SDL_UpdateTexture.restype = c_int
        S.SDL_RenderClear.argtypes = [c_void_p]
        S.SDL_RenderClear.restype = c_int
        S.SDL_RenderCopy.argtypes = [c_void_p, c_void_p, c_void_p, c_void_p]
        S.SDL_RenderCopy.restype = c_int
        S.SDL_RenderPresent.argtypes = [c_void_p]
        S.SDL_RenderPresent.restype = None
        S.SDL_PumpEvents.restype = None
        S.SDL_DestroyTexture.argtypes = [c_void_p]
        S.SDL_DestroyRenderer.argtypes = [c_void_p]
        S.SDL_DestroyWindow.argtypes = [c_void_p]
        S.SDL_ShowCursor.argtypes = [c_int]
        S.SDL_ShowCursor.restype = c_int

    def _err(self) -> str:
        msg = self.S.SDL_GetError()
        return msg.decode(errors="replace") if msg else "?"

    def _driver(self) -> str:
        d = self.S.SDL_GetCurrentVideoDriver()
        return d.decode(errors="replace") if d else "?"

    def _create_window(self, w: int, h: int) -> c_void_p:
        flag_sets = [
            SDL_WINDOW_FULLSCREEN_DESKTOP | SDL_WINDOW_OPENGL,
            SDL_WINDOW_FULLSCREEN | SDL_WINDOW_OPENGL,
            SDL_WINDOW_FULLSCREEN_DESKTOP,
            SDL_WINDOW_FULLSCREEN,
            SDL_WINDOW_OPENGL | SDL_WINDOW_SHOWN,
            SDL_WINDOW_SHOWN,
        ]
        for flags in flag_sets:
            win = self.S.SDL_CreateWindow(
                b"Cybermesh", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, w, h, flags
            )
            if win:
                self.log(f"window created with flags=0x{flags:04x}")
                return win
            self.log(f"window flags=0x{flags:04x} failed: {self._err()}")
        raise RuntimeError(f"SDL_CreateWindow failed: {self._err()}")

    def _create_renderer(self) -> c_void_p:
        for flags in (SDL_RENDERER_ACCELERATED, SDL_RENDERER_SOFTWARE, 0):
            ren = self.S.SDL_CreateRenderer(self.window, -1, flags)
            if ren:
                self.log(f"renderer created with flags=0x{flags:04x}")
                return ren
            self.log(f"renderer flags=0x{flags:04x} failed: {self._err()}")
        raise RuntimeError(f"SDL_CreateRenderer failed: {self._err()}")

    def hide_cursor(self) -> None:
        try:
            self.S.SDL_ShowCursor(0)
        except Exception:  # noqa: BLE001
            pass

    def present(self, rgba_bytes: bytes) -> None:
        self.S.SDL_UpdateTexture(self.texture, None, rgba_bytes, self._pitch)
        self.S.SDL_RenderClear(self.renderer)
        self.S.SDL_RenderCopy(self.renderer, self.texture, None, None)
        self.S.SDL_RenderPresent(self.renderer)

    def pump(self) -> None:
        self.S.SDL_PumpEvents()

    def close(self) -> None:
        try:
            if getattr(self, "texture", None):
                self.S.SDL_DestroyTexture(self.texture)
            if getattr(self, "renderer", None):
                self.S.SDL_DestroyRenderer(self.renderer)
            if getattr(self, "window", None):
                self.S.SDL_DestroyWindow(self.window)
            self.S.SDL_Quit()
        except Exception:  # noqa: BLE001
            pass
