"""SDL / pygame display init for embedded Linux (exFAT SD, no kmsdrm)."""

from __future__ import annotations

import os
from typing import List, Tuple

import pygame


def _driver_candidates() -> List[str]:
    preferred = os.environ.get("SDL_VIDEODRIVER")
    drivers = [
        preferred,
        "fbcon",
        "directfb",
        "kmsdrm",
        "KMSDRM",
        "x11",
        "wayland",
        "dummy",
    ]
    out: List[str] = []
    for drv in drivers:
        if drv and drv not in out:
            out.append(drv)
    return out


def _prepare_fbcon() -> None:
    if not os.environ.get("SDL_FBDEV") and os.path.exists("/dev/fb0"):
        os.environ["SDL_FBDEV"] = "/dev/fb0"
    os.environ.setdefault("SDL_NOMOUSE", "1")


def init_display(width: int, height: int) -> Tuple[pygame.Surface, str]:
    """Try SDL video drivers until pygame can open the handheld screen."""
    errors: List[str] = []
    for drv in _driver_candidates():
        if drv == "fbcon":
            _prepare_fbcon()
        os.environ["SDL_VIDEODRIVER"] = drv
        pygame.quit()
        try:
            pygame.init()
            screen = pygame.display.set_mode((width, height))
            pygame.display.set_caption("Cybermesh")
            return screen, drv
        except pygame.error as exc:
            errors.append(f"{drv}: {exc}")
            pygame.quit()
    raise RuntimeError("No SDL video driver worked. " + "; ".join(errors))
