"""System info helpers: battery level and ALSA system volume (RG35xx stock Linux)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

_PCT_RE = re.compile(r"\[(\d+)%\]")
_CTRL_RE = re.compile(r"'([^']+)'")


def read_battery() -> Optional[Tuple[int, bool]]:
    """Return (percent, charging) for the first battery, or None if unavailable."""
    base = Path("/sys/class/power_supply")
    if not base.is_dir():
        return None
    for p in sorted(base.iterdir()):
        try:
            ptype = (p / "type").read_text(encoding="utf-8").strip()
            if ptype != "Battery":
                continue
            cap = int((p / "capacity").read_text(encoding="utf-8").strip())
        except Exception:  # noqa: BLE001
            continue
        charging = False
        status_path = p / "status"
        if status_path.exists():
            try:
                charging = status_path.read_text(encoding="utf-8").strip() in ("Charging", "Full")
            except Exception:  # noqa: BLE001
                pass
        return (max(0, min(100, cap)), charging)
    return None


class SystemVolume:
    """Read/adjust the system playback volume via amixer (ALSA)."""

    _CANDIDATES = (
        "Master", "PCM", "Speaker", "Playback", "DAC",
        "Headphone", "digital volume", "Line Out", "SPK",
    )

    def __init__(self, log: Callable[[str], None] = print) -> None:
        self.log = log
        self._amixer = shutil.which("amixer")
        self.control: Optional[str] = None
        if self._amixer:
            self.control = self._detect_control()
            self.log(f"SystemVolume: amixer={self._amixer} control={self.control}")
        else:
            self.log("SystemVolume: amixer not found — volume control disabled")

    @property
    def available(self) -> bool:
        return self._amixer is not None and self.control is not None

    def _detect_control(self) -> Optional[str]:
        for name in self._CANDIDATES:
            if self._get(name) is not None:
                return name
        try:
            out = subprocess.check_output(
                [self._amixer, "scontrols"], text=True, stderr=subprocess.DEVNULL, timeout=4.0
            )
        except Exception:  # noqa: BLE001
            return None
        for line in out.splitlines():
            m = _CTRL_RE.search(line)
            if m and self._get(m.group(1)) is not None:
                return m.group(1)
        return None

    def _get(self, control: str) -> Optional[int]:
        if not self._amixer:
            return None
        try:
            out = subprocess.check_output(
                [self._amixer, "sget", control],
                text=True, stderr=subprocess.DEVNULL, timeout=4.0,
            )
        except Exception:  # noqa: BLE001
            return None
        m = _PCT_RE.search(out)
        return int(m.group(1)) if m else None

    def get_volume(self) -> Optional[int]:
        if self.control is None:
            return None
        return self._get(self.control)

    def set_volume(self, pct: int) -> Optional[int]:
        if not self.available:
            return None
        pct = max(0, min(100, int(pct)))
        try:
            subprocess.run(
                [self._amixer, "-q", "sset", self.control, f"{pct}%", "unmute"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4.0,
            )
        except Exception:  # noqa: BLE001
            return None
        return self.get_volume()

    def change(self, delta: int) -> Optional[int]:
        cur = self.get_volume()
        if cur is None:
            return None
        return self.set_volume(cur + delta)
