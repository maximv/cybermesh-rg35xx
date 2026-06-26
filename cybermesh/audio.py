"""UI sound effects — synthesized WAV, played via aplay (ALSA)."""

from __future__ import annotations

import array
import io
import math
import os
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

SAMPLE_RATE = 22050


def _env_enabled() -> bool:
    raw = (os.environ.get("CYBERMESH_SOUND") or os.environ.get("MESHTASTIC_SOUND") or "1").lower()
    return raw not in ("0", "false", "off", "no")


def load_sound_enabled(port_dir: Optional[Path]) -> Optional[bool]:
    if port_dir is None:
        return None
    path = port_dir / "sound.txt"
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip().lower()
    except OSError:
        return None
    if raw in ("1", "on", "yes", "true", "вкл"):
        return True
    if raw in ("0", "off", "no", "false", "выкл"):
        return False
    return None


def save_sound_enabled(port_dir: Path, enabled: bool) -> None:
    try:
        port_dir.mkdir(parents=True, exist_ok=True)
        (port_dir / "sound.txt").write_text("1\n" if enabled else "0\n", encoding="utf-8")
    except OSError:
        pass


DEFAULT_VOLUME = 80


def load_volume(port_dir: Optional[Path]) -> Optional[int]:
    if port_dir is None:
        return None
    path = port_dir / "volume.txt"
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    try:
        return max(0, min(100, int(raw)))
    except ValueError:
        return None


def save_volume(port_dir: Path, volume: int) -> None:
    try:
        port_dir.mkdir(parents=True, exist_ok=True)
        (port_dir / "volume.txt").write_text(f"{max(0, min(100, int(volume)))}\n", encoding="utf-8")
    except OSError:
        pass


def _scale_wav(data: bytes, gain: float) -> bytes:
    """Return WAV bytes with 16-bit PCM samples scaled by gain (0..1)."""
    if gain >= 0.999:
        return data
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            nch, sw, fr = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
            frames = wf.readframes(wf.getnframes())
    except Exception:  # noqa: BLE001
        return data
    if sw != 2:
        return data
    pcm = array.array("h")
    pcm.frombytes(frames)
    g = max(0.0, gain)
    for i in range(len(pcm)):
        pcm[i] = int(max(-32768, min(32767, pcm[i] * g)))
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(nch)
        wf.setsampwidth(sw)
        wf.setframerate(fr)
        wf.writeframes(pcm.tobytes())
    return out.getvalue()


def _mix_tone(
    buf: array.array,
    start_s: float,
    duration_s: float,
    freq: float,
    volume: float = 0.2,
) -> None:
    i0 = int(start_s * SAMPLE_RATE)
    n = int(duration_s * SAMPLE_RATE)
    fade = min(n // 4, int(SAMPLE_RATE * 0.008))
    for i in range(n):
        idx = i0 + i
        if idx < 0 or idx >= len(buf):
            continue
        t = i / SAMPLE_RATE
        env = 1.0
        if fade > 0:
            if i < fade:
                env = i / fade
            elif i >= n - fade:
                env = max(0.0, (n - i) / fade)
        sample = env * volume * math.sin(2.0 * math.pi * freq * t)
        buf[idx] = max(-1.0, min(1.0, buf[idx] + sample))


def _mix_noise(buf: array.array, start_s: float, duration_s: float, volume: float = 0.06) -> None:
    import random

    i0 = int(start_s * SAMPLE_RATE)
    n = int(duration_s * SAMPLE_RATE)
    rng = random.Random(42)
    for i in range(n):
        idx = i0 + i
        if idx < 0 or idx >= len(buf):
            continue
        env = 1.0 - (i / max(1, n))
        buf[idx] = max(-1.0, min(1.0, buf[idx] + (rng.random() * 2.0 - 1.0) * volume * env))


def _pcm_to_wav(samples: array.array) -> bytes:
    pcm = array.array("h")
    for s in samples:
        pcm.append(int(max(-1.0, min(1.0, s)) * 32767))
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    return bio.getvalue()


def _gen_click_wav(freq: float, duration_s: float, volume: float) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    buf = array.array("f", [0.0] * n)
    fade = max(1, n // 5)
    for i in range(n):
        t = i / SAMPLE_RATE
        env = 1.0
        if i < fade:
            env = i / fade
        else:
            env = math.exp(-(i - fade) / max(1.0, n * 0.35))
        buf[i] = env * volume * math.sin(2.0 * math.pi * freq * t)
    return _pcm_to_wav(buf)


def synth_modem_connect(duration_s: float = 2.9) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    buf = array.array("f", [0.0] * n)
    _mix_tone(buf, 0.05, 0.25, 350.0, 0.08)
    _mix_tone(buf, 0.25, 0.55, 2225.0, 0.22)
    _mix_tone(buf, 0.75, 0.35, 2100.0, 0.18)
    t = 1.05
    freqs = (1200.0, 1650.0, 2100.0, 1270.0, 1800.0, 2225.0, 1400.0, 2000.0)
    while t < duration_s - 0.45:
        f = freqs[int(t * 8) % len(freqs)]
        _mix_tone(buf, t, 0.055, f, 0.14)
        t += 0.065
    _mix_noise(buf, 1.0, 1.4, 0.04)
    _mix_tone(buf, duration_s - 0.4, 0.35, 2400.0, 0.1)
    return _pcm_to_wav(buf)


def synth_modem_disconnect(duration_s: float = 1.15) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    buf = array.array("f", [0.0] * n)
    for i, f in enumerate((2100.0, 1850.0, 1600.0, 1300.0, 950.0)):
        _mix_tone(buf, 0.08 + i * 0.14, 0.12, f, 0.16)
    _mix_noise(buf, 0.55, 0.35, 0.07)
    _mix_tone(buf, 0.75, 0.25, 480.0, 0.08)
    return _pcm_to_wav(buf)


class SfxPlayer:
    """Non-blocking UI sounds (aplay on Linux / ALSA)."""

    def __init__(
        self,
        log: Callable[[str], None] = print,
        *,
        enabled: Optional[bool] = None,
        volume: Optional[int] = None,
        port_dir: Optional[Path] = None,
    ) -> None:
        self.log = log
        if enabled is None and port_dir is not None:
            enabled = load_sound_enabled(port_dir)
        if volume is None and port_dir is not None:
            volume = load_volume(port_dir)
        self._volume = DEFAULT_VOLUME if volume is None else max(0, min(100, int(volume)))
        self._on = _env_enabled() if enabled is None else bool(enabled)
        self._aplay = shutil.which("aplay")
        self._lock = threading.Lock()
        self._last_nav = 0.0
        self._last_type = 0.0
        self._nav_wav = _gen_click_wav(720.0, 0.022, 0.11)
        self._type_wav = _gen_click_wav(1380.0, 0.016, 0.085)
        self._modem_up = synth_modem_connect()
        self._modem_down = synth_modem_disconnect()
        if self._on and self._aplay:
            self.log(f"SfxPlayer: aplay={self._aplay}")
        elif self._on:
            self.log("SfxPlayer: aplay not found — sounds disabled")
            self._on = False

    @property
    def enabled(self) -> bool:
        return self._on

    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, pct: int) -> int:
        self._volume = max(0, min(100, int(pct)))
        return self._volume

    def set_enabled(self, on: bool) -> None:
        if not _env_enabled():
            self._on = False
            return
        if on and not self._aplay:
            self.log("SfxPlayer: aplay not found — cannot enable sound")
            self._on = False
            return
        self._on = bool(on)

    def toggle(self) -> bool:
        self.set_enabled(not self.enabled)
        return self.enabled

    def _play_wav(self, data: bytes, *, blocking: bool = False) -> None:
        if not self._on or not self._aplay or self._volume <= 0:
            return
        data = _scale_wav(data, self._volume / 100.0)

        def _run() -> None:
            try:
                proc = subprocess.Popen(
                    [self._aplay, "-q", "-t", "wav", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.communicate(input=data, timeout=12.0)
            except Exception:  # noqa: BLE001
                pass

        if blocking:
            _run()
        else:
            threading.Thread(target=_run, daemon=True).start()

    def modem_connect(self, *, blocking: bool = False) -> None:
        self._play_wav(self._modem_up, blocking=blocking)

    def modem_disconnect(self, *, blocking: bool = False) -> None:
        self._play_wav(self._modem_down, blocking=blocking)

    def nav_click(self) -> None:
        now = time.monotonic()
        if now - self._last_nav < 0.045:
            return
        self._last_nav = now
        self._play_wav(self._nav_wav)

    def type_click(self) -> None:
        now = time.monotonic()
        if now - self._last_type < 0.028:
            return
        self._last_type = now
        self._play_wav(self._type_wav)

    def play_for_action(self, action: str, *, view: str, menu_open: bool) -> None:
        if view == "kbd" and not menu_open:
            return
        if action in ("UP", "DOWN", "LEFT", "RIGHT", "PGUP", "PGDN", "CHPREV", "CHNEXT", "START", "MENU"):
            self.nav_click()

    def play_kbd_action(self, action: str) -> None:
        if action in ("A", "X"):
            self.type_click()
        elif action in ("UP", "DOWN", "LEFT", "RIGHT", "Y", "PGUP", "PGDN"):
            self.nav_click()
        elif action == "B":
            self.type_click()
