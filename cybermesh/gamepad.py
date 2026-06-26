"""Map RG35xx gamepad / keyboard evdev events to curses key codes."""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    InputDevice = None  # type: ignore[misc, assignment]
    ecodes = None
    list_devices = None


# Linux input codes used by many handhelds.
_BTN_A = 304
_BTN_B = 305
_BTN_X = 307
_BTN_Y = 308
_BTN_START = 315
_BTN_SELECT = 314
_BTN_MODE = 316

_DPAD_MAP = {
    ecodes.KEY_UP if ecodes else 103: "up",
    ecodes.KEY_DOWN if ecodes else 108: "down",
    ecodes.KEY_LEFT if ecodes else 105: "left",
    ecodes.KEY_RIGHT if ecodes else 106: "right",
}


class GamepadReader:
    def __init__(self, inject: Callable[[int], None]) -> None:
        self._inject = inject
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._device_path: Optional[str] = None

    @staticmethod
    def _pick_device() -> Optional[str]:
        if InputDevice is None or list_devices is None:
            return None
        preferred = []
        fallback = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                name = (dev.name or "").lower()
                caps = dev.capabilities(verbose=False)
                has_keys = ecodes.EV_KEY in caps
                if not has_keys:
                    continue
                keys = caps.get(ecodes.EV_KEY, [])
                gamepadish = any(
                    code in keys
                    for code in (
                        ecodes.BTN_GAMEPAD if hasattr(ecodes, "BTN_GAMEPAD") else 0,
                        _BTN_A,
                        ecodes.KEY_UP if ecodes else 103,
                    )
                )
                if not gamepadish:
                    continue
                if any(x in name for x in ("anbernic", "retro", "gamepad", "rg35")):
                    preferred.append(path)
                else:
                    fallback.append(path)
            except (OSError, PermissionError):
                continue
        if preferred:
            return preferred[0]
        if fallback:
            return fallback[0]
        return None

    def start(self) -> bool:
        if InputDevice is None:
            return False
        path = os.environ.get("MESHTASTIC_GAMEPAD")
        if not path:
            path = self._pick_device()
        if not path:
            return False
        self._device_path = path
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(path,), daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, path: str) -> None:
        try:
            dev = InputDevice(path)
            dev.grab()
        except (OSError, PermissionError):
            try:
                dev = InputDevice(path)
            except (OSError, PermissionError):
                return

        hat_x = 0
        hat_y = 0

        def emit_hat() -> None:
            if hat_y < 0:
                self._inject_key("up")
            elif hat_y > 0:
                self._inject_key("down")
            if hat_x < 0:
                self._inject_key("left")
            elif hat_x > 0:
                self._inject_key("right")

        for event in dev.read_loop():
            if self._stop.is_set():
                break
            if event.type != ecodes.EV_KEY or event.value != 1:
                if event.type == ecodes.EV_ABS and event.value in (-1, 0, 1):
                    if event.code in (ecodes.ABS_HAT0X, 0):
                        hat_x = event.value
                        emit_hat()
                    elif event.code in (ecodes.ABS_HAT0Y, 1):
                        hat_y = event.value
                        emit_hat()
                continue

            code = event.code
            if code in _DPAD_MAP:
                self._inject_key(_DPAD_MAP[code])
            elif code == _BTN_A:
                self._inject_ord(10)  # Enter
            elif code == _BTN_B:
                self._inject_ord(27)  # Esc
            elif code == _BTN_X:
                self._inject_ord(ord("s"))  # Send menu
            elif code == _BTN_Y:
                self._inject_ord(ord("n"))  # Nodes
            elif code in (_BTN_START, _BTN_MODE):
                self._inject_ord(ord("m"))  # Main menu
            elif code == _BTN_SELECT:
                self._inject_ord(ord("r"))  # Rescan

    def _inject_key(self, name: str) -> None:
        mapping = {
            "up": curses_key("KEY_UP"),
            "down": curses_key("KEY_DOWN"),
            "left": curses_key("KEY_LEFT"),
            "right": curses_key("KEY_RIGHT"),
        }
        key = mapping.get(name)
        if key is not None:
            self._inject(key)

    def _inject_ord(self, ch: int) -> None:
        self._inject(ch)


def curses_key(name: str) -> int:
    import curses

    return getattr(curses, name)
