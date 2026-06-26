"""Turn panel backlight / framebuffer on and off (RG35xx stock Linux)."""

from __future__ import annotations

from pathlib import Path
from typing import List

_FB_BLANK = Path("/sys/class/graphics/fb0/blank")
_FB_BLANK_CON = Path("/sys/class/graphics/fbcon/blank")


def _backlight_dirs() -> List[Path]:
    base = Path("/sys/class/backlight")
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def _write(path: Path, text: str) -> bool:
    try:
        path.write_text(text, encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001
        return False


class Backlight:
    def __init__(self, log=None) -> None:
        self.log = log or (lambda _m: None)
        self._saved_brightness: dict[str, str] = {}
        self._off = False

    @property
    def is_off(self) -> bool:
        return self._off

    def off(self) -> bool:
        ok = False
        for bl in _backlight_dirs():
            br = bl / "brightness"
            if br.exists():
                try:
                    self._saved_brightness[str(bl)] = br.read_text(encoding="utf-8").strip()
                except Exception:  # noqa: BLE001
                    pass
            if _write(bl / "bl_power", "4"):
                ok = True
            elif br.exists() and _write(br, "0"):
                ok = True
        if _write(_FB_BLANK, "4"):
            ok = True
        if _write(_FB_BLANK_CON, "4"):
            ok = True
        self._off = ok
        if ok:
            self.log("display off")
        else:
            self.log("display off: no sysfs control found")
        return ok

    def on(self) -> bool:
        ok = False
        for bl in _backlight_dirs():
            if _write(bl / "bl_power", "0"):
                ok = True
            br = bl / "brightness"
            saved = self._saved_brightness.get(str(bl))
            if saved and br.exists():
                if _write(br, saved):
                    ok = True
            elif br.exists():
                max_path = bl / "max_brightness"
                try:
                    mx = int(max_path.read_text(encoding="utf-8").strip())
                    if _write(br, str(max(mx // 2, 1))):
                        ok = True
                except Exception:  # noqa: BLE001
                    pass
        if _write(_FB_BLANK, "0"):
            ok = True
        if _write(_FB_BLANK_CON, "0"):
            ok = True
        self._off = False
        if ok:
            self.log("display on")
        return ok

    def toggle(self) -> bool:
        if self._off:
            return self.on()
        return self.off()
