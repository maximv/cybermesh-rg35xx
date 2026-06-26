"""Gamepad / keyboard input via evdev -> high level action queue.

Actions: UP DOWN LEFT RIGHT A B X Y START SELECT MENU PGUP PGDN CHPREV CHNEXT SCREEN_OFF
Map pan: poll InputReader.map_pan_vector() while the map view is open.
Hold START+MENU together to force-quit (works even if the UI thread is hung).
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    from evdev import InputDevice, ecodes, list_devices
except Exception:  # noqa: BLE001
    InputDevice = None
    ecodes = None
    list_devices = None

# Button codes for Anbernic RG35xx family (measured via evdev probe).
BTN_MAP: Dict[int, str] = {
    304: "A",
    305: "B",
    306: "Y",
    307: "X",
    308: "PGUP",    # L1
    309: "PGDN",    # R1
    310: "SELECT",
    311: "START",
    312: "MENU",    # M button (RG35xx). NB: M emits 312 AND 354 per press —
    314: "CHPREV",  # L2          map only ONE of them or MENU toggles twice.
    315: "CHNEXT",  # R2
    316: "MENU",    # M / MODE (other firmwares; not emitted alongside 312 there)
}


def _menu_code_overrides() -> "list[int]":
    """Extra evdev codes to treat as the MENU button (CYBERMESH_MENU_CODE=312,139)."""
    raw = os.environ.get("CYBERMESH_MENU_CODE") or os.environ.get("MESHTASTIC_MENU_CODE") or ""
    out: List[int] = []
    for tok in raw.replace(",", " ").split():
        try:
            out.append(int(tok, 0))
        except ValueError:
            pass
    return out


for _code in _menu_code_overrides():
    BTN_MAP[_code] = "MENU"

DIR_ACTIONS = frozenset({"UP", "DOWN", "LEFT", "RIGHT"})
FORCE_QUIT_CHORD = frozenset({"START", "MENU"})
# L1 + R1 + SELECT held together -> capture a screenshot.
SCREENSHOT_CHORD = frozenset({"PGUP", "PGDN", "SELECT"})
CHORD_BUTTONS = frozenset({"START", "MENU"}) | SCREENSHOT_CHORD
STICK_DEADZONE = 0.14

# Linux KEY_POWER and friends (gpio-keys on handhelds).
POWER_CODES: set[int] = {102, 116, 143, 205, 244}  # HOME, POWER, WAKEUP, SUSPEND, ...
if ecodes is not None:
    for name in ("KEY_POWER", "KEY_HOME", "KEY_SUSPEND", "KEY_WAKEUP", "KEY_SLEEP"):
        code = getattr(ecodes, name, None)
        if code is not None:
            POWER_CODES.add(code)

# Hardware volume keys (gpio-keys): KEY_VOLUMEUP=115, KEY_VOLUMEDOWN=114.
VOLUME_CODES: Dict[int, str] = {115: "VOLUP", 114: "VOLDOWN"}
if ecodes is not None:
    for name, act in (("KEY_VOLUMEUP", "VOLUP"), ("KEY_VOLUMEDOWN", "VOLDOWN")):
        code = getattr(ecodes, name, None)
        if code is not None:
            VOLUME_CODES[code] = act

KEY_MAP: Dict[int, str] = {}
STICK_AXIS: Dict[int, str] = {}
if ecodes is not None:
    KEY_MAP = {
        ecodes.KEY_UP: "UP",
        ecodes.KEY_DOWN: "DOWN",
        ecodes.KEY_LEFT: "LEFT",
        ecodes.KEY_RIGHT: "RIGHT",
        ecodes.KEY_ENTER: "A",
        ecodes.KEY_ESC: "B",
        ecodes.KEY_SPACE: "A",
    }
    key_menu = getattr(ecodes, "KEY_MENU", None)
    if key_menu is not None:
        KEY_MAP[key_menu] = "MENU"
    for attr, name in (
        ("ABS_X", "lx"),
        ("ABS_Y", "ly"),
        ("ABS_Z", "lz"),    # RG35xx left stick HORIZONTAL (event1, measured)
        ("ABS_RX", "lw"),   # RG35xx left stick VERTICAL (event1, measured)
        ("ABS_RY", "ry"),   # right stick vertical
        ("ABS_RZ", "rx"),   # right stick horizontal
    ):
        code = getattr(ecodes, attr, None)
        if code is not None:
            STICK_AXIS[code] = name


def _map_stick_layout() -> str:
    mode = (os.environ.get("CYBERMESH_MAP_STICK") or os.environ.get("MESHTASTIC_MAP_STICK") or "pro").lower()
    return "std" if mode in ("std", "standard", "0", "false") else "pro"


def _norm_axis(value: int, lo: int, hi: int, invert_y: bool = False) -> float:
    if hi <= lo:
        return 0.0
    center = (lo + hi) / 2.0
    half = max(1.0, (hi - lo) / 2.0)
    n = (value - center) / half
    if invert_y:
        n = -n
    if abs(n) < STICK_DEADZONE:
        return 0.0
    sign = 1.0 if n > 0 else -1.0
    scaled = (abs(n) - STICK_DEADZONE) / (1.0 - STICK_DEADZONE)
    return sign * min(1.0, scaled)


def _norm_hat(val: int) -> int:
    if -1 <= val <= 1:
        return val
    # 0=left/up, 1=center, 2=right/down on some drivers
    return max(-1, min(1, val - 1))


def _stick_pick(sticks: Dict[str, float], primary: str, alt: str) -> float:
    a = sticks.get(primary, 0.0)
    b = sticks.get(alt, 0.0)
    return a if abs(a) >= abs(b) else b


def _left_stick_for_pan(sticks: Dict[str, float]) -> Tuple[float, float]:
    """Return (vertical, horizontal) for the left stick; right stick is ignored."""
    if _map_stick_layout() == "std":
        vertical = _stick_pick(sticks, "ly", "lz")
        horizontal = _stick_pick(sticks, "lx", "lw")
        return vertical, horizontal
    # RG35xx pro (event1): left stick = ABS_Z (horizontal) + ABS_RX (vertical).
    # Negate so pushing up/left yields positive vertical/horizontal (matches D-pad).
    vertical = -_stick_pick(sticks, "lw", "ly")
    horizontal = -_stick_pick(sticks, "lz", "lx")
    return vertical, horizontal


def is_force_quit_chord(held: Set[str]) -> bool:
    return FORCE_QUIT_CHORD.issubset(held)


def combine_pan_vector(
    hat_x: int,
    hat_y: int,
    sticks: Dict[str, float],
    held: Optional[Dict[str, bool]] = None,
) -> Tuple[float, float]:
    """Return pan direction; x=right, y=down, each in [-1, 1].

    RG35xx pro: left stick is ABS_Z (vertical) + ABS_RX (horizontal) on event1.
    Right stick (ABS_RY/RZ) is ignored for map pan.
    """
    held = held or {}
    hx = _norm_hat(hat_x)
    hy = _norm_hat(hat_y)
    if held.get("LEFT"):
        hx = -1
    elif held.get("RIGHT"):
        hx = 1
    if held.get("UP"):
        hy = -1
    elif held.get("DOWN"):
        hy = 1

    stick_v, stick_h = _left_stick_for_pan(sticks)

    # x = right, y = down. D-pad and stick share one mapping (no axis swap):
    #   UP -> pan up, DOWN -> pan down, LEFT -> pan left, RIGHT -> pan right.
    # Stick vertical uses +stick_v so its up/down matches the D-pad.
    vx = -float(hx) + stick_h
    vy = -float(hy) + stick_v

    mag = math.hypot(vx, vy)
    if mag > 1.0:
        vx /= mag
        vy /= mag
    return vx, vy


class InputReader:
    def __init__(
        self,
        actions: "queue.Queue[str]",
        log: Callable[[str], None] = print,
        port_dir: Optional[Path] = None,
    ) -> None:
        self.actions = actions
        self.log = log
        self._port_dir = port_dir
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()
        self._last_screen_off = 0.0
        self._last_screenshot = 0.0
        self._lock = threading.Lock()
        self._held_chord: Set[str] = set()
        self._hat_x = 0
        self._hat_y = 0
        self._sticks: Dict[str, float] = {
            "lx": 0.0, "ly": 0.0, "lz": 0.0, "lw": 0.0, "rx": 0.0, "ry": 0.0,
        }
        self._held_dir: Dict[str, bool] = {d: False for d in DIR_ACTIONS}
        self._axis_range: Dict[int, Tuple[int, int]] = {}
        self._gamepad_paths: Set[str] = set()
        self._logged_codes: Set[int] = set()
        # code -> [action, next_fire_time]; drives software autorepeat for volume keys.
        self._vol_held: Dict[int, List] = {}

    @staticmethod
    def available() -> bool:
        return InputDevice is not None and list_devices is not None

    def map_pan_vector(self) -> Tuple[float, float]:
        with self._lock:
            return combine_pan_vector(
                self._hat_x, self._hat_y, dict(self._sticks), dict(self._held_dir)
            )

    def stick_pan_vector(self) -> Tuple[float, float]:
        """Pan vector from the analog stick only (D-pad is used for node selection)."""
        with self._lock:
            return combine_pan_vector(0, 0, dict(self._sticks), None)

    def hat_state(self) -> Tuple[int, int]:
        """Current D-pad vector as (hx, hy), each in {-1,0,1}; x=right, y=down.

        Combines the analog hat (ABS_HAT0*) with KEY-based d-pads so diagonals
        (e.g. up+left) are reported as a single vector like (-1, -1).
        """
        with self._lock:
            hx = _norm_hat(self._hat_x)
            hy = _norm_hat(self._hat_y)
            if self._held_dir.get("LEFT"):
                hx = -1
            elif self._held_dir.get("RIGHT"):
                hx = 1
            if self._held_dir.get("UP"):
                hy = -1
            elif self._held_dir.get("DOWN"):
                hy = 1
        return hx, hy

    def _candidate_devices(self) -> List[str]:
        forced = os.environ.get("CYBERMESH_GAMEPAD") or os.environ.get("MESHTASTIC_GAMEPAD")
        if forced:
            return [forced]
        if list_devices is None:
            return []
        found: List[str] = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities()
            except Exception:  # noqa: BLE001
                continue
            if ecodes.EV_KEY in caps or ecodes.EV_ABS in caps:
                found.append(path)
        return found

    @staticmethod
    def _is_gamepad(dev: InputDevice) -> bool:
        if ecodes is None:
            return False
        keys = dev.capabilities().get(ecodes.EV_KEY, [])
        return any(k in BTN_MAP for k in keys)

    @staticmethod
    def _is_pan_device(dev: InputDevice) -> bool:
        if InputReader._is_gamepad(dev):
            return True
        if ecodes is None:
            return False
        abs_caps = dev.capabilities().get(ecodes.EV_ABS, [])
        codes = {code for code, _ in abs_caps}
        wanted = set(STICK_AXIS) | {ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y}
        return bool(codes & wanted)

    def start(self) -> bool:
        if not self.available():
            self.log("evdev not available")
            return False
        devices = self._candidate_devices()
        if not devices:
            self.log("no input devices found")
            return False
        for path in devices:
            t = threading.Thread(target=self._read_loop, args=(path,), daemon=True)
            t.start()
            self._threads.append(t)
        t_pwr = threading.Thread(target=self._power_loop, daemon=True)
        t_pwr.start()
        self._threads.append(t_pwr)
        t_vol = threading.Thread(target=self._volume_repeat_loop, daemon=True)
        t_vol.start()
        self._threads.append(t_vol)
        self.log(f"input devices: {devices} map_stick={_map_stick_layout()}")
        return True

    def stop(self) -> None:
        self._stop.set()

    def _emit(self, action: str) -> None:
        if action == "SCREEN_OFF":
            now = time.time()
            if now - self._last_screen_off < 0.4:
                return
            self._last_screen_off = now
        self.actions.put(action)

    def _track_chord(self, action: str, pressed: bool) -> None:
        if action not in CHORD_BUTTONS:
            return
        with self._lock:
            if pressed:
                self._held_chord.add(action)
            else:
                self._held_chord.discard(action)
            held = set(self._held_chord)
        if is_force_quit_chord(held):
            self._force_quit()
        if SCREENSHOT_CHORD.issubset(held):
            self._maybe_screenshot()

    def _maybe_screenshot(self) -> None:
        now = time.time()
        if now - self._last_screenshot < 1.0:
            return
        self._last_screenshot = now
        self._emit("SCREENSHOT")

    def _force_quit(self) -> None:
        self.log("FORCE QUIT: START+MENU")
        if self._port_dir is not None:
            try:
                from .logutil import release_pidfile

                release_pidfile(self._port_dir)
            except Exception:  # noqa: BLE001
                pass
        os._exit(2)

    def _set_held_dir(self, action: str, pressed: bool) -> None:
        if action not in DIR_ACTIONS:
            return
        with self._lock:
            self._held_dir[action] = pressed

    def _learn_axis_ranges(self, dev: InputDevice) -> None:
        if ecodes is None:
            return
        abs_caps = dev.capabilities().get(ecodes.EV_ABS, [])
        codes = {code for code, _info in abs_caps} if abs_caps else set()
        for code in list(STICK_AXIS.keys()) + [ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y]:
            if code not in codes:
                continue
            try:
                info = dev.absinfo(code)
                self._axis_range[code] = (info.min, info.max)
            except Exception:  # noqa: BLE001
                pass

    def _power_loop(self) -> None:
        if list_devices is None:
            return
        for path in list_devices():
            try:
                dev = InputDevice(path)
                caps = dev.capabilities()
                keys = caps.get(ecodes.EV_KEY, [])
                if not any(k in POWER_CODES for k in keys):
                    continue
            except Exception:  # noqa: BLE001
                continue
            try:
                dev = InputDevice(path)
                self.log(f"power listener: {path} ({dev.name})")
                for event in dev.read_loop():
                    if self._stop.is_set():
                        break
                    if event.type != ecodes.EV_KEY or event.value != 1:
                        continue
                    if event.code in POWER_CODES:
                        self._emit("SCREEN_OFF")
            except OSError:
                pass

    def _read_loop(self, path: str) -> None:
        try:
            dev = InputDevice(path)
        except Exception as exc:  # noqa: BLE001
            self.log(f"open {path} failed: {exc}")
            return

        is_pan = self._is_pan_device(dev)
        with self._lock:
            self._learn_axis_ranges(dev)
            if is_pan:
                self._gamepad_paths.add(path)

        try:
            for event in dev.read_loop():
                if self._stop.is_set():
                    break
                if event.type == ecodes.EV_KEY:
                    self._handle_key(event)
                elif event.type == ecodes.EV_ABS:
                    self._handle_abs(event, is_pan)
        except OSError:
            self.log(f"input device {path} disconnected")

    def _handle_key(self, event) -> None:
        code = event.code
        value = event.value
        pressed = value != 0
        if code in BTN_MAP:
            action = BTN_MAP[code]
            self._track_chord(action, pressed)
            # value: 1=press, 0=release, 2=autorepeat. Action buttons fire once on
            # press; only directions are allowed to auto-repeat for held scrolling.
            if value == 1 or (value == 2 and action in DIR_ACTIONS):
                self._emit(action)
            return
        if code in POWER_CODES:
            if value == 1:
                self._emit("SCREEN_OFF")
            return
        if code in VOLUME_CODES:
            action = VOLUME_CODES[code]
            # gpio-keys usually don't auto-repeat, so we drive repeats in software.
            if value == 1:
                self._emit(action)
                with self._lock:
                    self._vol_held[code] = [action, time.monotonic() + 0.4]
            elif value == 0:
                with self._lock:
                    self._vol_held.pop(code, None)
            return
        if code not in KEY_MAP:
            if value == 1:
                self._log_unmapped(code)
            return
        action = KEY_MAP[code]
        if action in DIR_ACTIONS:
            self._set_held_dir(action, pressed)
        self._track_chord(action, pressed)
        if value == 1 or (value == 2 and action in DIR_ACTIONS):
            self._emit(action)

    def _volume_repeat_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.04)
            now = time.monotonic()
            with self._lock:
                items = list(self._vol_held.items())
            for code, st in items:
                if now >= st[1]:
                    self._emit(st[0])
                    with self._lock:
                        if code in self._vol_held:
                            self._vol_held[code][1] = now + 0.12

    def _log_unmapped(self, code: int) -> None:
        if code in self._logged_codes:
            return
        self._logged_codes.add(code)
        self.log(f"unmapped input code {code} (set CYBERMESH_MENU_CODE={code} to use it as MENU)")

    def _default_axis_range(self, code: int) -> Tuple[int, int]:
        if code in self._axis_range:
            return self._axis_range[code]
        if ecodes is not None and _map_stick_layout() == "pro":
            for attr in ("ABS_Z", "ABS_RX", "ABS_RY", "ABS_RZ"):
                if getattr(ecodes, attr, None) == code:
                    return (-4096, 4096)
        return (-32768, 32767)

    def _handle_abs(self, event, is_pan: bool) -> None:
        if ecodes is None or not is_pan:
            return
        code = event.code
        val = event.value
        emit: Optional[str] = None

        if code == ecodes.ABS_HAT0X:
            val = _norm_hat(val)
            with self._lock:
                old = self._hat_x
                self._hat_x = val
            if val < 0 and old >= 0:
                emit = "LEFT"
            elif val > 0 and old <= 0:
                emit = "RIGHT"
        elif code == ecodes.ABS_HAT0Y:
            val = _norm_hat(val)
            with self._lock:
                old = self._hat_y
                self._hat_y = val
            if val < 0 and old >= 0:
                emit = "UP"
            elif val > 0 and old <= 0:
                emit = "DOWN"
        elif code in STICK_AXIS:
            name = STICK_AXIS[code]
            with self._lock:
                lo, hi = self._default_axis_range(code)
            n = _norm_axis(val, lo, hi, invert_y=False)
            with self._lock:
                self._sticks[name] = n

        if emit is not None:
            self._emit(emit)
