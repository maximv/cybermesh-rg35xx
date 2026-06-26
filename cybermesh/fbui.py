"""Pillow-rendered UI presented through the system-SDL ('mali') backend."""

from __future__ import annotations

import math
import queue
import re
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

from PIL import Image, ImageDraw

from .backlight import Backlight
from .fonts import Fonts
from .geo import format_distance
from .sysinfo import SystemVolume, read_battery
from .mapview import MapView
from .chat_types import ChatMessage, SEND_FAILED, SEND_PENDING
from .radio import (
    BleDevice,
    NODE_SORT_DEFAULT,
    NODE_SORT_MODES,
    RadioManager,
    node_sort_label,
)
from .audio import save_sound_enabled, save_volume
from .i18n import (
    lang_name,
    load_language,
    save_language,
    set_language,
    t,
    toggle_language,
)

KbdTarget = Tuple[str, int]  # ("channel"|"dm"|"chname"|"psk"|"filter"|"setting", index/peer)


def _read_text_lines(path: Path) -> List[str]:
    """Read a user-edited text file; tolerate UTF-8, BOM, or Windows Cyrillic."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").splitlines()


def load_saved_devices(port_dir: Path) -> List[BleDevice]:
    path = port_dir / "device.txt"
    if not path.exists():
        return []
    out: List[BleDevice] = []
    for line in _read_text_lines(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        addr = parts[0]
        name = parts[1] if len(parts) > 1 else t("saved.device")
        out.append(BleDevice(name=name, address=addr))
    return out


def save_device(port_dir: Path, dev: BleDevice) -> None:
    path = port_dir / "device.txt"
    existing = load_saved_devices(port_dir)
    if any(d.address.lower() == dev.address.lower() for d in existing):
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{dev.address} {dev.name}\n")
    except Exception:  # noqa: BLE001
        pass


from .theme import (
    COL_ACCENT,
    COL_ACCENT2,
    COL_DIM,
    COL_ERR,
    COL_HI,
    COL_ME,
    COL_PANEL,
    COL_SEL,
    COL_TEXT,
    draw_background,
    draw_footer_bar,
    draw_header,
    draw_list_item,
    draw_menu_frame,
    draw_panel_box,
    header_title,
)

KBD_LAYERS = [
    ("РУС", ["йцукенгшщзхъ", "фывапролджэ", "ячсмитьбюё"]),
    ("LAT", ["qwertyuiop", "asdfghjkl", "zxcvbnm"]),
    ("123", ["1234567890", "-/:;()@&", ".,?!'\"+=*#"]),
    # Single code-point emoji only (so per-cell indexing stays one glyph = one char).
    ("EMO", ["😀😃😄😁😆😅😂🙂😉😊", "😍😘😎🤔😴😭😡👍👎🙏", "🔥💯✨⭐🎉👀💀🚀📡🔋"]),
]
# Meshtastic carries text in a single Data payload (max 237 bytes). Emoji and
# Cyrillic take several UTF-8 bytes each, so the budget is counted in bytes.
MAX_MSG_BYTES = 237
_PRELOAD_DOTS = (".", "..", "...")
ROLE_CYCLE = [1, 2, 0]  # PRIMARY, SECONDARY, DISABLED
ROLE_LABELS = {0: "OFF", 1: "PRIMARY", 2: "SECONDARY"}


def _preload_dots() -> str:
    return _PRELOAD_DOTS[int(time.time() * 2.5) % 3]


def _with_preloader(text: str) -> str:
    base = re.sub(r"[\.…]+$", "", text)
    return base + _preload_dots()


def load_presets(port_dir: Path) -> List[str]:
    path = port_dir / "presets.txt"
    if not path.exists():
        return [t("preset.online"), "OK"]
    lines = [ln.strip() for ln in _read_text_lines(path)]
    return [ln for ln in lines if ln] or ["OK"]


class FbUI:
    def __init__(
        self,
        screen,
        radio: RadioManager,
        presets: List[str],
        port_dir: Path,
        log=print,
        sfx=None,
    ) -> None:
        self.screen = screen
        self.radio = radio
        self.presets = presets
        self.port_dir = port_dir
        set_language(load_language(port_dir))
        self.saved = load_saved_devices(port_dir)
        self.log = log
        self.sfx = sfx
        self.W = screen.width
        self.H = screen.height
        self.fonts = Fonts(log=log)

        self.view = "scan"
        self.sel = 0
        self.scroll = 0
        self.menu_open = False
        self.menu_sel = 0
        self._rebuild_menu()
        self.status = ""
        self.status_until = 0.0
        self.running = True
        self._dirty = True
        self._last_nav = 0.0

        self.active_channel = 0
        self.msg_sel = 0
        self.dm_peer: Optional[int] = None

        self.kbd_text = ""
        self.kbd_layer = 0
        self.kbd_row = 0
        self.kbd_col = 0
        self.kbd_shift = False
        self.kbd_target: KbdTarget = ("channel", 0)
        self.kbd_return = "chat"
        self.kbd_reply_id: Optional[int] = None
        self.kbd_reply_label = ""

        self.ctx_ids: List[str] = []
        self.ctx_sel = 0
        self.ctx_msg: Optional[ChatMessage] = None
        self.ctx_return = "chat"
        self._ctx_node_num: Optional[int] = None
        self._ctx_node_favorite = False

        self.info_lines: List[str] = []
        self.info_return = "chat"
        self.info_title = ""
        self._pre_menu_view = "chat"
        self._my_node_info = False
        self._auto_scan_done = False
        self.nodes_filter = ""
        self.nodes_sort = NODE_SORT_DEFAULT

        self.ch_edit_idx = 0
        self.ch_edit_name = ""
        self.ch_edit_role = 2
        self.ch_edit_psk = ""
        self.ch_field = 0  # 0=name 1=role 2=psk

        self.map_view = MapView(port_dir / "assets" / "tiles", self.W, self.H)
        self.map_node_idx = -1
        self._dpad_prev: Tuple[int, int] = (0, 0)
        self._dpad_last = 0.0
        self.settings_fields: List = []
        self.settings_sel = 0
        self.settings_scroll = 0
        self.settings_return = "chat"
        self._last_frame = None
        self.backlight = Backlight(log=log)
        self.sysaudio = SystemVolume(log=log)

        self._chat_follow = True
        self._volume: int = self.sfx.volume if self.sfx is not None else 80
        self._bat_pct: Optional[int] = None
        self._bat_charging = False
        self._bat_poll_t = 0.0

        self.header_h = 34
        self.footer_h = 28
        self.row_h = 30
        self.chat_line_h = 20

        if self.saved:
            self.radio.connect(self.saved[0].address)
            self.view = "chat"
        else:
            self.radio.start_scan()

    def _filtered_nodes(self):
        nodes = self.radio.filter_nodes(self.radio.node_list(), self.nodes_filter)
        return self.radio.sort_nodes(nodes, self.nodes_sort)

    def _nodes_row_h(self) -> int:
        return 40

    def _nodes_visible_rows(self) -> int:
        extra = 58 if self.nodes_filter else 50
        return max(3, (self.H - self.header_h - self.footer_h - extra) // self._nodes_row_h())

    def _cycle_nodes_sort(self, direction: int) -> None:
        modes = NODE_SORT_MODES
        try:
            idx = modes.index(self.nodes_sort)
        except ValueError:
            idx = 0
        self.nodes_sort = modes[(idx + direction) % len(modes)]
        self.sel = 0
        self.scroll = 0
        self.set_status(t("status.sort", label=node_sort_label(self.nodes_sort)), 2.5)

    def _clamp_nodes_sel(self) -> None:
        nodes = self._filtered_nodes()
        if not nodes:
            self.sel = 0
            self.scroll = 0
        else:
            self.sel = max(0, min(len(nodes) - 1, self.sel))

    def _list_rows(self) -> int:
        return max(3, (self.H - self.header_h - self.footer_h - 16) // self.row_h)

    def _chat_rows(self) -> int:
        return max(3, (self.H - self.header_h - self.footer_h - 40) // 22)

    def _page_step(self) -> int:
        return max(3, self._chat_rows() - 1)

    def set_status(self, text: str, seconds: float = 2.5) -> None:
        self.status = text
        self.status_until = time.time() + seconds
        self._dirty = True

    def _save_screenshot(self) -> None:
        img = getattr(self, "_last_frame", None)
        if img is None:
            return
        try:
            shots = self.port_dir / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            path = shots / time.strftime("cybermesh-%Y%m%d-%H%M%S.png")
            img.convert("RGB").save(path)
            self.log(f"screenshot saved: {path}")
            self.set_status(t("status.screenshot", name=path.name), 3)
        except Exception as exc:  # noqa: BLE001
            self.log(f"screenshot failed: {exc}")
            self.set_status(t("status.screenshot_err", err=str(exc)[:40]), 3)

    def _send_mark(self, msg: ChatMessage) -> str:
        if not msg.from_me:
            return ""
        if msg.send_status == SEND_PENDING:
            return " …"
        if msg.send_status == SEND_FAILED:
            return " !"
        return ""

    def _do_send(
        self,
        text: str,
        kind: str,
        idx: int,
        return_view: Optional[str] = None,
        reply_id: Optional[int] = None,
    ) -> None:
        text = text.strip()
        if not text:
            return
        self.set_status(t("send.sending"), 60)
        self._dirty = True
        if return_view:
            self.view = return_view

        def _work() -> None:
            if kind == "channel":
                err = self.radio.send_text(text, idx, reply_id=reply_id)
            elif kind == "dm":
                err = self.radio.send_dm(text, idx, reply_id=reply_id)
            else:
                return
            if err:
                self.set_status(t("send.error", err=err[:48]), 4)
            else:
                preview = text.replace("\n", " ")[:24]
                if reply_id is not None:
                    label = t("send.reply_sent")
                elif kind == "channel":
                    label = t("send.sent")
                else:
                    label = t("send.dm_sent")
                self.set_status(f"{label}: {preview}", 2.5)
            self._dirty = True

        threading.Thread(target=_work, daemon=True).start()

    def _chat_top(self) -> int:
        return self.header_h + 34

    def _chat_bottom(self) -> int:
        return self.H - self.footer_h - 8

    def _message_parts(self, msg: ChatMessage) -> Tuple[List[str], Optional[str]]:
        who = t("chat.you") if msg.from_me else msg.sender
        stamp = time.strftime("%H:%M", time.localtime(msg.ts))
        mark = self._send_mark(msg)
        main = self._wrap(f"[{stamp}] {who}: {msg.text}{mark}", self.W - 24)[:2]
        reply_line: Optional[str] = None
        if msg.reply_id:
            name = self.radio.reply_target_name(
                msg.reply_id,
                channel=msg.channel,
                is_dm=msg.is_dm,
                peer_num=msg.peer_num,
            )
            reply_line = t("chat.reply_to", name=name or "?")
        return main, reply_line

    def _message_block_h(self, msg: ChatMessage) -> int:
        main, reply_line = self._message_parts(msg)
        h = len(main) * self.chat_line_h
        if reply_line:
            h += self.chat_line_h
        return h + 2

    def _message_lines(self, msg: ChatMessage) -> List[str]:
        main, reply_line = self._message_parts(msg)
        lines = list(main)
        if reply_line:
            lines.append(reply_line)
        return lines

    VOL_STEP = 5

    def _adjust_volume(self, direction: int) -> None:
        newv = max(0, min(100, self._volume + self.VOL_STEP * direction))
        self._volume = newv
        if self.sfx is not None:
            self.sfx.set_volume(newv)
        save_volume(self.port_dir, newv)
        # Best-effort: also nudge the system mixer if a working control exists.
        if self.sysaudio.available:
            self.sysaudio.set_volume(newv)
        self.set_status(t("status.volume", pct=newv), 1.5)

    def _header_status(self) -> str:
        now = time.time()
        if now - self._bat_poll_t > 10.0:
            self._bat_poll_t = now
            bat = read_battery()
            if bat is not None:
                self._bat_pct, self._bat_charging = bat
        parts: List[str] = []
        parts.append(t("hud.clock", time=time.strftime("%H:%M")))
        parts.append(t("hud.volume", pct=self._volume))
        if self._bat_pct is not None:
            chg = "+" if self._bat_charging else ""
            parts.append(t("hud.battery", pct=self._bat_pct, chg=chg))
        return "  ".join(parts)

    def _scroll_to_latest(self) -> None:
        msgs = self._current_messages()
        if msgs:
            self.msg_sel = len(msgs) - 1
        else:
            self.msg_sel = 0
        self.scroll = 0
        self._chat_follow = True

    def _show_chat(self) -> None:
        """Return to the main channel chat, scrolled to the newest message."""
        self.view = "chat"
        self._scroll_to_latest()

    def _current_messages(self) -> List[ChatMessage]:
        if self.view == "dm" and self.dm_peer is not None:
            return self.radio.dm_messages(self.dm_peer)
        return self.radio.channel_messages(self.active_channel)

    def _clamp_msg_sel(self) -> None:
        msgs = self._current_messages()
        if not msgs:
            self.msg_sel = 0
        else:
            self.msg_sel = max(0, min(len(msgs) - 1, self.msg_sel))

    def _ensure_msg_visible(self) -> None:
        msgs = self._current_messages()
        if not msgs:
            self.scroll = 0
            return
        if self.msg_sel >= len(msgs) - 1:
            self.scroll = 0
            return
        y = self._chat_bottom()
        y_top = self._chat_top()
        for idx in range(len(msgs) - 1, -1, -1):
            block_h = self._message_block_h(msgs[idx])
            y -= block_h
            if idx == self.msg_sel:
                if y < y_top:
                    self.scroll = max(self.scroll, len(msgs) - 1 - idx)
                return
            if y < y_top:
                break
        self.scroll = max(0, len(msgs) - 1 - self.msg_sel)

    def _cycle_channel(self, direction: int) -> None:
        enabled = self.radio.enabled_channels()
        if not enabled:
            return
        idxs = [c.index for c in enabled]
        if self.active_channel not in idxs:
            self.active_channel = idxs[0]
        else:
            i = idxs.index(self.active_channel)
            self.active_channel = idxs[(i + direction) % len(idxs)]
        self._scroll_to_latest()
        self._ensure_msg_visible()

    def _rebuild_menu(self) -> None:
        sound = t("state.on") if (self.sfx and self.sfx.enabled) else t("state.off")
        self.menu_ids = [
            "send",
            "messages",
            "nodes",
            "channels",
            "map",
            "mynode",
            "settings",
            "sound",
            "lang",
            "rescan",
            "disconnect",
            "quit",
        ]
        self.menu_items = [
            t("menu.send"),
            t("menu.messages"),
            t("menu.nodes"),
            t("menu.channels"),
            t("menu.map"),
            t("menu.mynode"),
            t("menu.settings"),
            t("menu.sound", state=sound),
            t("menu.lang", lang=lang_name()),
            t("menu.rescan"),
            t("menu.disconnect"),
            t("menu.quit"),
        ]

    def _select_menu_id(self, menu_id: str) -> None:
        if menu_id in self.menu_ids:
            self.menu_sel = self.menu_ids.index(menu_id)

    def _toggle_sound(self) -> None:
        if self.sfx is None:
            self.set_status(t("status.sound_unavailable"), 2.5)
            return
        on = self.sfx.toggle()
        save_sound_enabled(self.port_dir, on)
        self._rebuild_menu()
        self._select_menu_id("sound")
        self.set_status(t("status.sound_on") if on else t("status.sound_off"), 2.0)
        if on:
            self.sfx.nav_click()

    def _toggle_language(self) -> None:
        lang = toggle_language()
        save_language(self.port_dir, lang)
        self._rebuild_menu()
        self._select_menu_id("lang")
        self.set_status(t("status.lang_set"), 2.0)
        if self.sfx:
            self.sfx.nav_click()

    def run(self, actions: "queue.Queue[str]", reader=None) -> None:
        self.screen.hide_cursor()
        last_refresh = 0.0
        while self.running:
            try:
                action = actions.get(timeout=0.05)
                self._on_action(action)
            except queue.Empty:
                pass

            if self.view == "map" and not self.menu_open and not self.backlight.is_off:
                if self.map_view.update_anim():
                    self._dirty = True
                if reader is not None and self._map_dpad_tick(reader):
                    self._dirty = True
                if reader is not None and self._map_pan_tick(reader):
                    self._dirty = True

            if self._drain_radio():
                self._dirty = True

            if self.backlight.is_off:
                self.screen.pump()
                continue

            now = time.time()
            snap = self.radio.snapshot()
            ble_busy = snap.state in ("connecting", "scanning")
            map_view = self.view == "map"
            refresh_s = 0.35 if (ble_busy or map_view) else 1.0
            if self._dirty or now - last_refresh > refresh_s:
                self._render()
                last_refresh = now
                self._dirty = False

            self.screen.pump()

    def _drain_radio(self) -> bool:
        changed = False
        new_messages = False
        state_changed = False
        while True:
            try:
                ev = self.radio.events.get_nowait()
                changed = True
                if ev == "message":
                    new_messages = True
                elif ev == "state":
                    state_changed = True
            except Exception:
                break
        snap = self.radio.snapshot()
        state = snap.state
        if state == "connected":
            self._auto_scan_done = False
        if state == "connected" and self.view == "scan" and not self.menu_open:
            self.view = "chat"
            self.active_channel = 0
            self._scroll_to_latest()
        if state_changed and self.view == "nodeinfo" and self._my_node_info:
            self._refresh_my_node_info()
        if new_messages and self.view in ("chat", "dm"):
            msgs = self._current_messages()
            if msgs and (self._chat_follow or msgs[-1].from_me or self.msg_sel >= len(msgs) - 2):
                self._scroll_to_latest()
        if self.view in ("chat", "dm") and state in ("error", "disconnected"):
            self.view = "scan"
            if not self._auto_scan_done:
                self._auto_scan_done = True
                snap2 = self.radio.snapshot()
                if snap2.state not in ("scanning", "connecting"):
                    self.radio.start_scan()
        return changed

    def _nav_ok(self) -> bool:
        now = time.time()
        if now - self._last_nav < 0.13:
            return False
        self._last_nav = now
        return True

    def _dispatch_nav(self, base: str) -> None:
        if self.menu_open:
            self._action_menu(base)
        elif self.view == "scan":
            self._action_scan(base)
        elif self.view in ("chat", "dm"):
            self._action_chat(base)
        elif self.view == "send":
            self._action_send(base)
        elif self.view == "nodes":
            self._action_nodes(base)
        elif self.view == "dms":
            self._action_dms(base)
        elif self.view == "chcfg":
            self._action_chcfg(base)
        elif self.view == "chedit":
            self._action_chedit(base)
        elif self.view == "map":
            self._action_map(base)
        elif self.view == "ctx":
            self._action_ctx(base)
        elif self.view == "nodeinfo":
            self._action_nodeinfo(base)
        elif self.view == "settings":
            self._action_settings(base)

    def _maybe_nav_sound(self, action: str) -> None:
        if self.sfx is None:
            return
        if self.view == "map" and not self.menu_open and action in ("UP", "DOWN", "LEFT", "RIGHT"):
            return
        if self.view == "kbd" and not self.menu_open:
            return
        self.sfx.play_for_action(action, view=self.view, menu_open=self.menu_open)

    def _on_action(self, action: str) -> None:
        if action == "SCREEN_OFF":
            self.backlight.toggle()
            self._dirty = not self.backlight.is_off
            return
        if action == "SCREENSHOT":
            self._save_screenshot()
            return
        if self.backlight.is_off:
            self.backlight.on()
        self._dirty = True
        if action in ("VOLUP", "VOLDOWN"):
            self._adjust_volume(1 if action == "VOLUP" else -1)
            return
        if action not in ("SCREEN_OFF",):
            self._maybe_nav_sound(action)
        if self.view == "kbd" and not self.menu_open:
            self._action_kbd(action)
            return
        if action in ("PGUP", "PGDN"):
            if self.menu_open:
                self._action_menu("UP" if action == "PGUP" else "DOWN")
                return
            if self.view == "map":
                self.map_view.zoom_delta(1 if action == "PGDN" else -1)
                return
            if self.view in ("chat", "dm"):
                msgs = self._current_messages()
                step = self._page_step()
                if action == "PGUP":
                    self.msg_sel = max(0, self.msg_sel - step)
                else:
                    self.msg_sel = min(len(msgs) - 1, self.msg_sel + step)
                self._chat_follow = self.msg_sel >= len(msgs) - 1
                self._ensure_msg_visible()
                return
            if self.view == "nodeinfo":
                step = max(3, self._info_rows() - 1)
                if action == "PGUP":
                    self.scroll = max(0, self.scroll - step)
                else:
                    self.scroll = min(self._info_max_scroll(), self.scroll + step)
                return
            base = "UP" if action == "PGUP" else "DOWN"
            for _ in range(5):
                self._last_nav = 0.0
                self._dispatch_nav(base)
            return
        if action in ("CHPREV", "CHNEXT") and self.view == "chat":
            self._cycle_channel(-1 if action == "CHPREV" else 1)
            return
        if action in ("CHPREV", "CHNEXT") and self.view == "nodes" and not self.menu_open:
            self._cycle_nodes_sort(-1 if action == "CHPREV" else 1)
            return
        if action in ("CHPREV", "CHNEXT") and self.view == "map" and not self.menu_open:
            self._cycle_map_node(-1 if action == "CHPREV" else 1)
            return
        if self.view == "map" and not self.menu_open and action in ("UP", "DOWN", "LEFT", "RIGHT"):
            # Node selection is driven by polling the D-pad as a single vector
            # (see _map_dpad_tick) so diagonals select a diagonal node.
            return
        if self.menu_open:
            self._action_menu(action)
            return
        if action in ("UP", "DOWN") and not self._nav_ok():
            return

        if action in ("START", "MENU"):
            self._pre_menu_view = self.view
            self._rebuild_menu()
            self.menu_open = True
            self.menu_sel = 0
            return

        if self.view == "scan":
            self._action_scan(action)
        elif self.view in ("chat", "dm"):
            self._action_chat(action)
        elif self.view == "send":
            self._action_send(action)
        elif self.view == "nodes":
            self._action_nodes(action)
        elif self.view == "dms":
            self._action_dms(action)
        elif self.view == "chcfg":
            self._action_chcfg(action)
        elif self.view == "chedit":
            self._action_chedit(action)
        elif self.view == "map":
            self._action_map(action)
        elif self.view == "ctx":
            self._action_ctx(action)
        elif self.view == "nodeinfo":
            self._action_nodeinfo(action)
        elif self.view == "settings":
            self._action_settings(action)

    def _info_rows(self) -> int:
        return max(4, (self.H - self.header_h - self.footer_h - 44) // 20)

    def _info_max_scroll(self) -> int:
        return max(0, len(self.info_lines) - self._info_rows())

    def _open_node_info(self, num: int, ret: str) -> None:
        self._my_node_info = False
        info = self.radio.get_node(num)
        title = info.short if info else self.radio.short_for_num(num)
        self.info_title = title
        self.info_lines = self.radio.node_detail_lines(num)
        self.info_return = ret
        self.scroll = 0
        self.view = "nodeinfo"

    def _open_my_node_info(self) -> None:
        short, long_name = self.radio.my_node_labels()
        self.info_title = short or t("mynode.title")
        if long_name and long_name != short:
            self.info_title = f"{short} — {long_name}"
        self._my_node_info = True
        self.info_lines = self.radio.my_node_detail_lines()
        self.info_return = self._pre_menu_view
        self.scroll = 0
        self.view = "nodeinfo"
        self.radio.refresh_position()

    def _refresh_my_node_info(self) -> None:
        if not self._my_node_info or self.view != "nodeinfo":
            return
        short, long_name = self.radio.my_node_labels()
        title = short or t("mynode.title")
        if long_name and long_name != short:
            title = f"{short} — {long_name}"
        self.info_title = title
        self.info_lines = self.radio.my_node_detail_lines()
        self._dirty = True

    def _action_menu(self, action: str) -> None:
        n = len(self.menu_items)
        if action == "UP":
            self.menu_sel = (self.menu_sel - 1) % n
        elif action == "DOWN":
            self.menu_sel = (self.menu_sel + 1) % n
        elif action in ("A",):
            self._activate_menu()
        elif action in ("B", "START", "MENU"):
            self.menu_open = False
            if self.view == "chat":
                self._scroll_to_latest()

    def _activate_menu(self) -> None:
        item = self.menu_ids[self.menu_sel]
        self.menu_open = False
        if item == "send":
            self.view = "send"
            self.sel = 0
        elif item == "messages":
            self.view = "dms"
            self.sel = 0
            self.scroll = 0
        elif item == "nodes":
            self.view = "nodes"
            self.sel = 0
            self.scroll = 0
        elif item == "channels":
            self.view = "chcfg"
            self.sel = 0
            self.scroll = 0
        elif item == "map":
            self.view = "map"
            self.map_node_idx = -1
            self.radio.refresh_position()
            self.map_view._kick_prefetch()
            self._dirty = True
        elif item == "mynode":
            self._open_my_node_info()
        elif item == "settings":
            self._open_settings()
        elif item == "sound":
            self._toggle_sound()
            self.menu_open = True
        elif item == "lang":
            self._toggle_language()
            self.menu_open = True
        elif item in ("rescan", "disconnect"):
            self.radio.disconnect()
            self.view = "scan"
            self.sel = 0
            self.radio.start_scan()
        elif item == "quit":
            self.running = False

    def _merged_devices(self, scanned: List[BleDevice]) -> List[BleDevice]:
        merged = list(self.saved)
        seen = {d.address.lower() for d in merged}
        for d in scanned:
            if d.address.lower() not in seen:
                merged.append(d)
                seen.add(d.address.lower())
        return merged

    def _action_scan(self, action: str) -> None:
        snap = self.radio.snapshot()
        state = snap.state
        scanned = snap.devices
        devices = self._merged_devices(scanned)
        if action == "SELECT":
            self._auto_scan_done = False
            self.radio.start_scan()
            return
        if state == "connecting":
            return
        if action == "UP":
            self.sel = max(0, self.sel - 1)
        elif action == "DOWN":
            self.sel = min(max(0, len(devices) - 1), self.sel + 1)
        elif action == "A" and devices:
            dev = devices[self.sel]
            save_device(self.port_dir, dev)
            self.radio.connect(dev.address)
            self._show_chat()

    CTX_LABEL_KEYS = {
        "reply": "ctx.reply",
        "dm": "ctx.dm",
        "info": "ctx.info",
        "cancel": "ctx.cancel",
        "fav_add": "ctx.fav_add",
        "fav_del": "ctx.fav_del",
        "resend": "ctx.resend",
    }

    def _ctx_labels(self) -> List[str]:
        return [t(self.CTX_LABEL_KEYS.get(i, i)) for i in self.ctx_ids]

    def _open_ctx(self, msg: ChatMessage, item_ids: List[str], ret: str) -> None:
        self.ctx_msg = msg
        self.ctx_ids = item_ids
        self.ctx_sel = 0
        self.ctx_return = ret
        self.view = "ctx"

    def _action_chat(self, action: str) -> None:
        msgs = self._current_messages()
        self._clamp_msg_sel()
        if action == "UP":
            self.msg_sel = max(0, self.msg_sel - 1)
            self._chat_follow = self.msg_sel >= len(msgs) - 1
            self._ensure_msg_visible()
        elif action == "DOWN":
            self.msg_sel = min(max(0, len(msgs) - 1), self.msg_sel + 1)
            self._chat_follow = self.msg_sel >= len(msgs) - 1
            self._ensure_msg_visible()
        elif action in ("X",):
            self._open_keyboard(("channel", self.active_channel) if self.view == "chat"
                                else ("dm", self.dm_peer or 0), self.view)
        elif action in ("Y",):
            if self.view == "dm":
                self._open_dm_peer_ctx()
            else:
                self.view = "nodes"
                self.sel = 0
                self.scroll = 0
        elif action == "B":
            if self.view == "dm":
                self.view = "dms"
            else:
                self._pre_menu_view = self.view
                self._rebuild_menu()
                self.menu_open = True
                self.menu_sel = 0
        elif action == "A" and msgs:
            msg = msgs[self.msg_sel]
            if msg.from_me:
                if msg.send_status in (SEND_FAILED, SEND_PENDING):
                    self._open_ctx(msg, ["resend", "cancel"], self.view)
                return
            if self.view == "chat":
                self._open_ctx(msg, ["reply", "dm", "info", "cancel"], "chat")
            else:
                self._open_ctx(msg, ["reply", "info", "cancel"], "dm")

    def _resend_message(self, msg: ChatMessage, ret: str) -> None:
        self.radio.drop_message(msg)
        self._chat_follow = True
        if msg.is_dm:
            peer = msg.peer_num if msg.peer_num is not None else (self.dm_peer or 0)
            self._do_send(msg.text, "dm", peer, ret, reply_id=msg.reply_id)
        else:
            self._do_send(msg.text, "channel", msg.channel, ret, reply_id=msg.reply_id)

    def _action_ctx(self, action: str) -> None:
        if action == "UP":
            self.ctx_sel = max(0, self.ctx_sel - 1)
        elif action == "DOWN":
            self.ctx_sel = min(len(self.ctx_ids) - 1, self.ctx_sel + 1)
        elif action in ("B",):
            self.view = self.ctx_return
        elif action == "A":
            choice = self.ctx_ids[self.ctx_sel]
            msg = self.ctx_msg
            ret = self.ctx_return
            self.view = ret
            if choice == "cancel":
                return
            if choice == "resend":
                if msg is not None:
                    self._resend_message(msg, ret)
                return
            if choice == "dm":
                if msg and msg.sender_num is not None:
                    self.dm_peer = msg.sender_num
                    self.view = "dm"
                    self.msg_sel = max(0, len(self._current_messages()) - 1)
                    self.scroll = 0
                elif self._ctx_node_num is not None:
                    self.dm_peer = self._ctx_node_num
                    self.view = "dm"
                    self.msg_sel = 0
                    self.scroll = 0
                return
            if choice in ("fav_add", "fav_del") and self._ctx_node_num is not None:
                fav = choice == "fav_add"
                err = self.radio.set_node_favorite(self._ctx_node_num, fav)
                if err:
                    self.set_status(t("send.error", err=err[:48]), 4)
                else:
                    label = t("fav.added") if fav else t("fav.removed")
                    self.set_status(label, 2.5)
                self.view = self.ctx_return
                return
            if choice == "info":
                num = self._ctx_node_num
                if msg and msg.sender_num is not None:
                    num = msg.sender_num
                if num is not None:
                    self._open_node_info(num, ret)
                else:
                    self.view = ret
                    self.set_status(t("status.no_node_data"), 3)
                return
            if choice == "reply" and msg is not None:
                if msg.msg_id is not None:
                    label = t("reply.label", sender=msg.sender)
                    if ret == "chat":
                        self._open_keyboard(
                            ("channel", self.active_channel),
                            "chat",
                            reply_id=msg.msg_id,
                            reply_label=label,
                        )
                    else:
                        peer = self.dm_peer or msg.peer_num or 0
                        self._open_keyboard(
                            ("dm", peer),
                            "dm",
                            reply_id=msg.msg_id,
                            reply_label=label,
                        )
                else:
                    quote = self.radio.reply_quote(msg.sender, msg.text)
                    if ret == "chat":
                        self._open_keyboard(("channel", self.active_channel), "chat", quote)
                    else:
                        peer = self.dm_peer or msg.peer_num or 0
                        self._open_keyboard(("dm", peer), "dm", quote)

    def _action_nodeinfo(self, action: str) -> None:
        if action == "UP":
            self.scroll = max(0, self.scroll - 1)
        elif action == "DOWN":
            self.scroll = min(self._info_max_scroll(), self.scroll + 1)
        elif action in ("B",):
            self.view = self.info_return
            self.scroll = 0

    # --- settings editor ---------------------------------------------------

    def _open_settings(self) -> None:
        self.settings_fields = self.radio.device_settings()
        self.settings_sel = 0
        self.settings_scroll = 0
        self.settings_return = self._pre_menu_view
        self.view = "settings"

    def _settings_save_current(self) -> None:
        if not self.settings_fields:
            return
        field = self.settings_fields[self.settings_sel]
        if not field.dirty:
            self.set_status(t("settings.no_change"), 1.5)
            return
        err = self.radio.apply_setting(field.key, field.pending)
        if err:
            field.pending = field.raw
            self.set_status(t("settings.error", err=err[:40]), 3)
            return
        field.raw = field.pending
        self.set_status(t("settings.saved"), 2)

    def _action_settings(self, action: str) -> None:
        fields = self.settings_fields
        if action == "UP":
            self.settings_sel = max(0, self.settings_sel - 1)
            self._ensure_settings_visible()
            return
        if action == "DOWN":
            self.settings_sel = min(max(0, len(fields) - 1), self.settings_sel + 1)
            self._ensure_settings_visible()
            return
        if action == "B":
            self.view = self.settings_return
            return
        if not fields:
            return
        field = fields[self.settings_sel]
        if action in ("LEFT", "RIGHT"):
            if field.kind in ("enum", "bool", "int"):
                field.cycle(-1 if action == "LEFT" else 1)
            return
        if action == "A":
            if field.kind == "text":
                self._open_keyboard(("setting", self.settings_sel), "settings",
                                    prefill=str(field.pending))
            elif field.kind == "int":
                self._open_keyboard(("setting", self.settings_sel), "settings",
                                    prefill=str(field.pending))
            else:
                self._settings_save_current()

    def _ensure_settings_visible(self) -> None:
        rows = self._list_rows()
        if self.settings_sel < self.settings_scroll:
            self.settings_scroll = self.settings_sel
        elif self.settings_sel >= self.settings_scroll + rows:
            self.settings_scroll = self.settings_sel - rows + 1

    def _draw_settings(self, d) -> None:
        self.fonts.draw(d, (12, self.header_h + 8), t("settings.title"), COL_ACCENT, "normal")
        fields = self.settings_fields
        if not fields:
            self.fonts.draw(d, (12, self.header_h + 40), t("settings.empty"), COL_DIM, "small")
            return
        top = self.header_h + 36
        rows = self._list_rows()
        self.settings_scroll = min(self.settings_scroll, max(0, len(fields) - rows))
        x, width = 10, self.W - 20
        for i in range(self.settings_scroll, min(len(fields), self.settings_scroll + rows)):
            f = fields[i]
            ry = top + (i - self.settings_scroll) * self.row_h
            box = (x, ry, x + width, ry + self.row_h - 4)
            draw_list_item(d, box, i == self.settings_sel)
            mark = "▸ " if i == self.settings_sel else "  "
            dirty = "*" if f.dirty else ""
            label = f"{mark}{f.label}: {f.display}{dirty}"
            self.fonts.draw(d, (x + 8, ry + 4), label[:64], COL_TEXT, "normal")

    def _action_send(self, action: str) -> None:
        if action == "UP":
            self.sel = max(0, self.sel - 1)
        elif action == "DOWN":
            self.sel = min(len(self.presets) - 1, self.sel + 1)
        elif action == "A":
            text = self.presets[self.sel]
            self._do_send(text, "channel", self.active_channel, "chat")
        elif action == "B":
            self._show_chat()

    def _action_nodes(self, action: str) -> None:
        nodes = self._filtered_nodes()
        visible = self._nodes_visible_rows()
        if action == "UP":
            self.sel = max(0, self.sel - 1)
            if self.sel < self.scroll:
                self.scroll = self.sel
        elif action == "DOWN":
            self.sel = min(max(0, len(nodes) - 1), self.sel + 1)
            if self.sel >= self.scroll + visible:
                self.scroll = self.sel - visible + 1
        elif action == "Y":
            self._open_keyboard(("filter", 0), "nodes", self.nodes_filter)
        elif action == "SELECT" and self.nodes_filter:
            self.nodes_filter = ""
            self.sel = 0
            self.scroll = 0
            self.set_status(t("status.filter_reset"), 2)
        elif action == "X" and nodes:
            node = nodes[self.sel]
            err = self.radio.set_node_favorite(node.num, not node.is_favorite)
            if err:
                self.set_status(t("send.error", err=err[:48]), 4)
            else:
                state = t("fav.added") if not node.is_favorite else t("fav.removed")
                self.set_status(f"{state}: {node.short}", 2.5)
        elif action == "A" and nodes:
            self._open_ctx_for_node(nodes[self.sel])
        elif action in ("B",):
            self.nodes_filter = ""
            self._show_chat()

    def _open_dm_peer_ctx(self) -> None:
        peer = self.dm_peer
        if peer is None:
            return
        info = self.radio.get_node(peer)
        is_fav = bool(info and info.is_favorite)
        self.ctx_msg = None
        fav_item = "fav_del" if is_fav else "fav_add"
        self.ctx_ids = ["info", fav_item, "cancel"]
        self.ctx_sel = 0
        self.ctx_return = "dm"
        self._ctx_node_num = peer
        self._ctx_node_favorite = is_fav
        self.view = "ctx"

    def _open_ctx_for_node(self, node) -> None:
        self.ctx_msg = None
        fav_item = "fav_del" if node.is_favorite else "fav_add"
        self.ctx_ids = ["dm", "info", fav_item, "cancel"]
        self.ctx_sel = 0
        self.ctx_return = "nodes"
        self._ctx_node_num = node.num
        self._ctx_node_favorite = node.is_favorite
        self.view = "ctx"

    def _action_dms(self, action: str) -> None:
        peers = self.radio.dm_peers()
        visible = self._list_rows()
        if action == "UP":
            self.sel = max(0, self.sel - 1)
            if self.sel < self.scroll:
                self.scroll = self.sel
        elif action == "DOWN":
            self.sel = min(max(0, len(peers) - 1), self.sel + 1)
            if self.sel >= self.scroll + visible:
                self.scroll = self.sel - visible + 1
        elif action == "A" and peers:
            self.dm_peer = peers[self.sel].peer_num
            self.view = "dm"
            self.msg_sel = max(0, len(self._current_messages()) - 1)
            self.scroll = 0
        elif action == "B":
            self._show_chat()

    def _action_chcfg(self, action: str) -> None:
        channels = self.radio.channels_list()
        visible = self._list_rows()
        if action == "UP":
            self.sel = max(0, self.sel - 1)
            if self.sel < self.scroll:
                self.scroll = self.sel
        elif action == "DOWN":
            self.sel = min(7, self.sel + 1)
            if self.sel >= self.scroll + visible:
                self.scroll = self.sel - visible + 1
        elif action == "A":
            ch = channels[self.sel]
            self.ch_edit_idx = ch.index
            self.ch_edit_name = ch.name
            self.ch_edit_role = ch.role if ch.role else 2
            self.ch_edit_psk = ""
            self.ch_field = 0
            self.view = "chedit"
        elif action == "B":
            self._show_chat()

    def _action_chedit(self, action: str) -> None:
        if action == "UP":
            self.ch_field = max(0, self.ch_field - 1)
        elif action == "DOWN":
            self.ch_field = min(2, self.ch_field + 1)
        elif action == "A":
            if self.ch_field == 0:
                self._open_keyboard(("chname", self.ch_edit_idx), "chedit", self.ch_edit_name)
            elif self.ch_field == 1:
                if self.ch_edit_role in ROLE_CYCLE:
                    i = ROLE_CYCLE.index(self.ch_edit_role)
                else:
                    i = 0
                self.ch_edit_role = ROLE_CYCLE[(i + 1) % len(ROLE_CYCLE)]
            elif self.ch_field == 2:
                self._open_keyboard(("psk", self.ch_edit_idx), "chedit", self.ch_edit_psk)
        elif action == "START":
            psk = self.ch_edit_psk.encode("utf-8") if self.ch_edit_psk else b""
            err = self.radio.write_channel(self.ch_edit_idx, self.ch_edit_name,
                                           self.ch_edit_role, psk)
            self.set_status(err or t("chan.saved"), 4 if err else 2.5)
            self.view = "chcfg"
        elif action == "SELECT":
            if self.ch_edit_idx > 0:
                err = self.radio.delete_channel(self.ch_edit_idx)
                self.set_status(err or t("chan.deleted"), 4 if err else 2.5)
            self.view = "chcfg"
        elif action == "B":
            self.view = "chcfg"

    def _map_nodes(self):
        return self.radio.map_nodes()

    def _selected_map_node(self):
        nodes = self._map_nodes()
        if not nodes or self.map_node_idx < 0 or self.map_node_idx >= len(nodes):
            return None
        return nodes[self.map_node_idx]

    def _cycle_map_node(self, direction: int) -> None:
        nodes = self._map_nodes()
        if not nodes:
            self.set_status(t("status.no_gps_nodes"), 2)
            return
        if self.map_node_idx < 0:
            self.map_node_idx = 0 if direction > 0 else len(nodes) - 1
        else:
            self.map_node_idx = (self.map_node_idx + direction) % len(nodes)
        self._focus_map_node(self.map_node_idx)

    def _focus_map_node(self, idx: int) -> None:
        nodes = self._map_nodes()
        if not nodes or idx < 0 or idx >= len(nodes):
            return
        self.map_node_idx = idx
        n = nodes[idx]
        self.map_view.animate_to(n.lat, n.lon)
        dist = f" {format_distance(n.distance_m)}" if n.distance_m is not None else ""
        self.set_status(f"{n.short}{dist}  {idx + 1}/{len(nodes)}", 3)

    def _map_ref_xy(self) -> Tuple[int, int]:
        """Screen point to measure node directions from: selection, then me, then centre."""
        cur = self._selected_map_node()
        if cur is not None and cur.lat is not None and cur.lon is not None:
            return self.map_view._screen_xy(cur.lat, cur.lon)
        snap = self.radio.snapshot()
        if snap.my_lat is not None and snap.my_lon is not None:
            return self.map_view._screen_xy(snap.my_lat, snap.my_lon)
        return (self.W // 2 + self.map_view.pan_x, self.map_view.map_h // 2 + self.map_view.pan_y)

    def _select_map_node_vec(self, ddx: int, ddy: int) -> None:
        if ddx == 0 and ddy == 0:
            return
        nodes = self._map_nodes()
        if not nodes:
            self.set_status(t("status.no_gps_nodes"), 2)
            return
        rx, ry = self._map_ref_xy()
        cur = self._selected_map_node()
        best_cone: Optional[int] = None
        best_cone_d = 0.0
        best_any: Optional[int] = None
        best_any_d = 0.0
        for i, n in enumerate(nodes):
            if cur is not None and n.num == cur.num:
                continue
            if n.lat is None or n.lon is None:
                continue
            nx, ny = self.map_view._screen_xy(n.lat, n.lon)
            vx, vy = nx - rx, ny - ry
            proj = vx * ddx + vy * ddy
            if proj <= 0:
                continue
            dist = math.hypot(vx, vy)
            if best_any is None or dist < best_any_d:
                best_any, best_any_d = i, dist
            # Prefer nodes within a 45° cone of the pressed direction.
            perp = abs(vx * ddy - vy * ddx)
            if perp <= proj and (best_cone is None or dist < best_cone_d):
                best_cone, best_cone_d = i, dist
        target = best_cone if best_cone is not None else best_any
        if target is None:
            self.set_status(t("status.no_gps_nodes"), 1.5)
            return
        self._focus_map_node(target)

    def _map_write_selected(self) -> None:
        n = self._selected_map_node()
        if n is None:
            self.set_status(t("status.no_node_selected"), 2)
            return
        self._open_keyboard(("dm", n.num), "map")

    def _map_dpad_tick(self, reader) -> bool:
        """Poll the D-pad as a single vector so diagonals (up+left, etc.) move to
        a diagonally located node in one step instead of two axis-aligned hops."""
        if not hasattr(reader, "hat_state"):
            return False
        hx, hy = reader.hat_state()
        now = time.time()
        if hx == 0 and hy == 0:
            self._dpad_prev = (0, 0)
            return False
        # Fire on a fresh direction immediately; repeat while held after a delay.
        if (hx, hy) != self._dpad_prev:
            self._dpad_prev = (hx, hy)
            self._dpad_last = now
            self._select_map_node_vec(hx, hy)
            return True
        if now - self._dpad_last >= 0.30:
            self._dpad_last = now
            self._select_map_node_vec(hx, hy)
            return True
        return False

    def _map_pan_tick(self, reader) -> bool:
        vx, vy = reader.stick_pan_vector()
        mag = math.hypot(vx, vy)
        if mag < 0.05:
            return False
        # Speed grows with stick / held direction deflection (2x sensitivity).
        speed = 10.0 + 70.0 * min(1.0, mag)
        dx = int((vx / mag) * speed)
        dy = int((vy / mag) * speed)
        if dx == 0 and dy == 0:
            dx = 1 if vx > 0 else (-1 if vx < 0 else 0)
            dy = 1 if vy > 0 else (-1 if vy < 0 else 0)
        self.map_view.pan(dx, dy)
        return True

    def _action_map(self, action: str) -> None:
        if action == "A":
            self._map_write_selected()
        elif action == "Y":
            lat, lon, _have_me = self.radio.map_anchor()
            if lat is not None and lon is not None:
                self.map_view.center_on(lat, lon)
        elif action == "X":
            theme = self.map_view.toggle_theme()
            name = t("map.theme_light") if theme == "light" else t("map.theme_dark")
            self.set_status(t("status.map_theme", theme=name), 2)
        elif action == "B":
            self._show_chat()

    def _open_keyboard(
        self,
        target: KbdTarget,
        ret: str,
        prefill: str = "",
        *,
        reply_id: Optional[int] = None,
        reply_label: str = "",
    ) -> None:
        self.kbd_target = target
        self.kbd_return = ret
        self.kbd_text = prefill
        self.kbd_reply_id = reply_id
        self.kbd_reply_label = reply_label
        self.kbd_row = 0
        self.kbd_col = 0
        self.view = "kbd"

    def _commit_setting_text(self, idx: int, text: str) -> None:
        if idx < 0 or idx >= len(self.settings_fields):
            return
        field = self.settings_fields[idx]
        if field.kind == "int":
            try:
                field.pending = int(text.strip() or "0")
            except ValueError:
                self.set_status(t("settings.error", err="not a number"), 2.5)
                return
        else:
            field.pending = text
        self.settings_sel = idx
        self._settings_save_current()

    def _clear_kbd_reply(self) -> None:
        self.kbd_reply_id = None
        self.kbd_reply_label = ""

    def _kbd_grid(self) -> List[str]:
        return KBD_LAYERS[self.kbd_layer][1]

    def _kbd_current_char(self) -> str:
        grid = self._kbd_grid()
        row = grid[self.kbd_row]
        ch = row[self.kbd_col]
        return ch.upper() if self.kbd_shift else ch

    def _kbd_submit(self) -> None:
        text = self.kbd_text.strip()
        kind, idx = self.kbd_target
        if kind == "chname":
            self.ch_edit_name = self.kbd_text
            self.view = self.kbd_return
            return
        if kind == "psk":
            self.ch_edit_psk = self.kbd_text
            self.view = self.kbd_return
            return
        if kind == "filter":
            self.nodes_filter = self.kbd_text.strip().lower()
            self.sel = 0
            self.scroll = 0
            self.view = self.kbd_return
            return
        if kind == "setting":
            self._commit_setting_text(idx, self.kbd_text)
            self.view = self.kbd_return
            return
        if not text:
            self._clear_kbd_reply()
            self.view = self.kbd_return
            return
        kind_str = "channel" if kind == "channel" else "dm"
        reply_id = self.kbd_reply_id
        self._do_send(text, kind_str, idx, self.kbd_return, reply_id=reply_id)
        self.kbd_text = ""
        self._clear_kbd_reply()

    def _kbd_used_bytes(self) -> int:
        return len(self.kbd_text.encode("utf-8"))

    def _kbd_insert(self, ch: str) -> None:
        """Append a character only if it fits the UTF-8 byte budget."""
        if self._kbd_used_bytes() + len(ch.encode("utf-8")) > MAX_MSG_BYTES:
            self.set_status(t("kbd.full", max=MAX_MSG_BYTES), 1.5)
            return
        self.kbd_text += ch

    def _action_kbd(self, action: str) -> None:
        if self.sfx is not None:
            self.sfx.play_kbd_action(action)
        grid = self._kbd_grid()
        if action == "UP":
            self.kbd_row = (self.kbd_row - 1) % len(grid)
        elif action == "DOWN":
            self.kbd_row = (self.kbd_row + 1) % len(grid)
        elif action == "LEFT":
            self.kbd_col = (self.kbd_col - 1) % len(grid[self.kbd_row])
        elif action == "RIGHT":
            self.kbd_col = (self.kbd_col + 1) % len(grid[self.kbd_row])
        elif action == "A":
            self._kbd_insert(self._kbd_current_char())
        elif action == "B":
            if self.kbd_text:
                self.kbd_text = self.kbd_text[:-1]
            else:
                self._clear_kbd_reply()
                self.view = self.kbd_return
        elif action == "X":
            self._kbd_insert(" ")
        elif action == "Y":
            self.kbd_layer = (self.kbd_layer + 1) % len(KBD_LAYERS)
            self.kbd_row = 0
            self.kbd_col = 0
        elif action == "PGUP":
            self.kbd_shift = not self.kbd_shift
        elif action == "PGDN":
            if self.kbd_text:
                self.kbd_text = self.kbd_text[:-1]
        elif action == "START":
            self._kbd_submit()
        elif action == "SELECT":
            self.kbd_text = ""
            self._clear_kbd_reply()
            self.view = self.kbd_return
        self.kbd_col = min(self.kbd_col, len(self._kbd_grid()[self.kbd_row]) - 1)

    def _render(self) -> None:
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        draw_background(d, self.W, self.H)
        snap = self.radio.snapshot()
        state, error, devices = snap.state, snap.error, snap.devices
        my_lat, my_lon = snap.my_lat, snap.my_lon

        draw_header(d, self.W, self.header_h, self.fonts, header_title(state), self._header_status())

        if self.menu_open:
            self._draw_menu(d)
        elif self.view == "scan":
            self._draw_scan(d, state, error, devices)
        elif self.view == "chat":
            self._draw_chat(d, state)
        elif self.view == "dm":
            self._draw_dm(d, state)
        elif self.view == "send":
            self._draw_list(d, t("list.send"), self.presets)
        elif self.view == "nodes":
            self._draw_nodes(d)
        elif self.view == "dms":
            self._draw_dms(d)
        elif self.view == "chcfg":
            self._draw_chcfg(d)
        elif self.view == "chedit":
            self._draw_chedit(d)
        elif self.view == "map":
            self._draw_map(img, my_lat, my_lon)
        elif self.view == "ctx":
            self._draw_ctx(d)
        elif self.view == "nodeinfo":
            self._draw_nodeinfo(d)
        elif self.view == "settings":
            self._draw_settings(d)
        elif self.view == "kbd":
            self._draw_kbd(d)

        self._draw_footer(d)
        self._last_frame = img
        self.screen.present(img.tobytes("raw", self.screen.raw_mode))

    def _draw_footer(self, d: ImageDraw.ImageDraw) -> None:
        if time.time() < self.status_until and self.status:
            text = self.status
        elif self.menu_open:
            text = t("footer.menu")
        elif self.view == "kbd":
            text = t("footer.kbd")
        elif self.view == "chat":
            text = t("footer.chat")
        elif self.view == "dm":
            text = t("footer.dm")
        elif self.view == "map":
            text = t("footer.map")
        elif self.view == "chcfg":
            text = t("footer.chcfg")
        elif self.view == "chedit":
            text = t("footer.chedit")
        elif self.view == "dms":
            text = t("footer.dms")
        elif self.view == "nodes":
            text = t("footer.nodes")
        elif self.view == "ctx":
            text = t("footer.ctx")
        elif self.view == "nodeinfo":
            text = t("footer.nodeinfo")
        elif self.view == "settings":
            text = t("footer.settings")
        else:
            text = t("footer.default")
        draw_footer_bar(d, self.W, self.H, self.footer_h, self.fonts, text)

    def _draw_scan(self, d, state, error, scanned) -> None:
        y = self.header_h + 10
        devices = self._merged_devices(scanned)
        if state == "connecting":
            self.fonts.draw(
                d, (12, y), _with_preloader(self._connect_status(state)), COL_TEXT, "normal"
            )
            return
        if not devices:
            if state == "scanning":
                self.fonts.draw(d, (12, y), _with_preloader(t("scan.searching_bt")), COL_TEXT, "normal")
            elif state == "error":
                self.fonts.draw(d, (12, y), t("scan.error"), COL_ERR, "normal")
                self.fonts.draw(d, (12, y + 26), (error or "?")[:60], COL_ERR, "small")
            else:
                self.fonts.draw(d, (12, y), t("scan.none_found"), COL_TEXT, "normal")
                self.fonts.draw(d, (12, y + 26), t("scan.none_hint"), COL_DIM, "small")
            return
        caption = t("scan.choose")
        if state == "scanning":
            caption = t("scan.choose_scanning")
        self.fonts.draw(d, (12, y), caption, COL_ACCENT, "normal")
        items = [f"{dev.name}  {dev.address}" for dev in devices]
        self._draw_items(d, items, self.header_h + 40, self.sel, self.scroll)

    def _draw_chat_messages(self, d, messages: List[ChatMessage], header: str) -> None:
        self.fonts.draw(d, (12, self.header_h + 6), header, COL_ACCENT, "normal")
        self._clamp_msg_sel()
        self._ensure_msg_visible()
        if not messages:
            self.fonts.draw(d, (12, self.header_h + 40), t("chat.no_messages"), COL_DIM, "small")
            return

        y_bottom = self._chat_bottom()
        y_top = self._chat_top()
        idx = len(messages) - 1
        skip = self.scroll
        while idx >= 0 and skip > 0:
            idx -= 1
            skip -= 1

        y = y_bottom
        drawn: List[Tuple[int, ChatMessage, List[str], Optional[str], int]] = []
        while idx >= 0:
            msg = messages[idx]
            main_lines, reply_line = self._message_parts(msg)
            block_h = len(main_lines) * self.chat_line_h + 2
            if reply_line:
                block_h += self.chat_line_h
            if y - block_h < y_top and drawn:
                break
            drawn.append((idx, msg, main_lines, reply_line, block_h))
            y -= block_h
            idx -= 1

        y = y_bottom
        for abs_idx, msg, main_lines, reply_line, block_h in drawn:
            y -= block_h
            color = COL_ME if msg.from_me else COL_TEXT
            line_color = COL_ERR if msg.send_status == SEND_FAILED else color
            line_y = y + 2
            for sub in main_lines:
                if abs_idx == self.msg_sel:
                    d.rectangle(
                        [8, line_y - 2, self.W - 8, line_y + self.chat_line_h - 2],
                        fill=COL_HI,
                    )
                self.fonts.draw(d, (12, line_y), sub, line_color, "small")
                line_y += self.chat_line_h
            if reply_line:
                self.fonts.draw(d, (12, line_y), reply_line, COL_DIM, "small")

    def _connect_status(self, state: str) -> str:
        if state != "connecting":
            return ""
        hint = self.radio.snapshot().connect_hint
        return hint or t("connect.ble")

    def _draw_chat(self, d, state) -> None:
        if state == "connecting":
            self.fonts.draw(
                d, (12, self.header_h + 12), _with_preloader(self._connect_status(state)),
                COL_TEXT, "normal",
            )
            return
        ch_name = self.radio.channel_name(self.active_channel)
        header = f"#{self.active_channel} {ch_name}"
        if any(m.send_status == SEND_PENDING for m in self._current_messages()):
            header += "  ^..."
        self._draw_chat_messages(d, self._current_messages(), header)

    def _draw_dm(self, d, state) -> None:
        if state == "connecting":
            self.fonts.draw(
                d, (12, self.header_h + 12), _with_preloader(self._connect_status(state)),
                COL_TEXT, "normal",
            )
            return
        peer = self.dm_peer or 0
        short = self.radio.name_for_num(peer)
        header = t("dm.header", short=short)
        if any(m.send_status == SEND_PENDING for m in self._current_messages()):
            header += "  ^..."
        self._draw_chat_messages(d, self._current_messages(), header)

    def _draw_dms(self, d) -> None:
        peers = self.radio.dm_peers()
        self.fonts.draw(d, (12, self.header_h + 8), t("dms.title"), COL_ACCENT, "normal")
        if not peers:
            self.fonts.draw(d, (12, self.header_h + 40), t("dms.empty"), COL_DIM, "small")
            return
        items = []
        for p in peers:
            ts = time.strftime("%H:%M", time.localtime(p.last_ts))
            items.append(f"{p.short}  [{ts}] {p.last_text}")
        self._draw_items(d, items, self.header_h + 40, self.sel, self.scroll)

    def _draw_nodes(self, d) -> None:
        all_nodes = self.radio.node_list()
        nodes = self._filtered_nodes()
        snap = self.radio.snapshot()
        self._clamp_nodes_sel()
        fav_n = sum(1 for n in all_nodes if n.is_favorite)
        if self.nodes_filter:
            title = t("nodes.title_filtered", n=len(nodes), m=len(all_nodes))
        else:
            title = t("nodes.title", n=len(all_nodes))
        if fav_n:
            title += f"  *{fav_n}"
        title += f"  [{node_sort_label(self.nodes_sort)}]"
        if snap.nodes_loading:
            title += "  …"
        self.fonts.draw(d, (12, self.header_h + 8), title[:58], COL_ACCENT, "normal")
        if self.nodes_filter:
            q = self.nodes_filter if len(self.nodes_filter) <= 28 else self.nodes_filter[:28] + "…"
            self.fonts.draw(d, (12, self.header_h + 28), t("nodes.search", q=q), COL_DIM, "small")
        list_y = self.header_h + (50 if self.nodes_filter else 42)
        if not nodes:
            if self.nodes_filter:
                hint = t("nodes.no_match")
            elif snap.nodes_loading:
                hint = t("nodes.loading")
            else:
                hint = t("nodes.empty")
            self.fonts.draw(d, (12, list_y), hint, COL_DIM, "small")
            return
        row_h = self._nodes_row_h()
        visible = self._nodes_visible_rows()
        x, width = 10, self.W - 20
        for i in range(self.scroll, min(len(nodes), self.scroll + visible)):
            node = nodes[i]
            line1, line2 = self.radio.node_row_lines(node)
            ry = list_y + (i - self.scroll) * row_h
            box = (x, ry, x + width, ry + row_h - 4)
            draw_list_item(d, box, i == self.sel)
            mark = "▸ " if i == self.sel else "  "
            self.fonts.draw(d, (x + 8, ry + 3), (mark + line1)[:58], COL_TEXT, "small")
            if line2:
                self.fonts.draw(d, (x + 8, ry + 19), line2[:58], COL_DIM, "small")

    def _draw_chcfg(self, d) -> None:
        self.fonts.draw(d, (12, self.header_h + 8), t("chcfg.title"), COL_ACCENT, "normal")
        items = [f"{c.index}: {c.name or '-'} [{c.role_name}]" for c in self.radio.channels_list()]
        self._draw_items(d, items, self.header_h + 40, self.sel, self.scroll)

    def _draw_chedit(self, d) -> None:
        self.fonts.draw(d, (12, self.header_h + 4), t("chedit.title", idx=self.ch_edit_idx),
                        COL_ACCENT, "normal")
        fields = [
            t("chedit.name", val=self.ch_edit_name or t("common.empty")),
            t("chedit.role", val=ROLE_LABELS.get(self.ch_edit_role, "?")),
            t("chedit.psk", val=self.ch_edit_psk or t("chedit.psk_none")),
        ]
        y = self.header_h + 36
        for i, f in enumerate(fields):
            box = [10, y, self.W - 10, y + 36]
            draw_panel_box(d, tuple(box), accent=(i == self.ch_field))
            self.fonts.draw(d, (16, y + 8), f, COL_TEXT, "normal")
            y += 44

    def _draw_map(self, img: Image.Image, my_lat, my_lon) -> None:
        d = ImageDraw.Draw(img)
        snap = self.radio.snapshot()
        positioned = self.radio.positioned_node_count()
        have_me = snap.my_lat is not None and snap.my_lon is not None
        selected = self._selected_map_node()
        title = t("map.title", z=self.map_view.zoom)
        theme_tag = t("map.tag_light") if self.map_view.theme == "light" else t("map.tag_dark")
        title += f" {theme_tag}"
        if selected:
            title += f"  {selected.short} {self.map_node_idx + 1}/{positioned}"
        elif have_me:
            title += t("map.hdr_count", n=positioned)
        elif positioned:
            title += t("map.hdr_nodes", n=positioned)
        elif snap.nodes_loading:
            title += t("map.hdr_loading")
        self.fonts.draw(d, (12, self.header_h + 4), title[:58], COL_ACCENT, "small")
        nodes = self.radio.node_list()
        my_num = snap.my_num
        map_h = self.H - self.header_h - self.footer_h - 28
        self.map_view.map_h = map_h
        my_short, my_long = self.radio.my_node_labels()
        map_img = self.map_view.render(
            nodes, my_lat, my_lon, self.fonts, my_num=my_num,
            nodes_loading=snap.nodes_loading,
            selected=selected,
            my_short=my_short,
            my_long=my_long,
        )
        if map_img.size != (self.W, map_h):
            map_img = map_img.resize((self.W, map_h))
        img.paste(map_img, (0, self.header_h + 24))

    def _draw_ctx(self, d) -> None:
        self.fonts.draw(d, (12, self.header_h + 8), t("ctx.title"), COL_ACCENT, "normal")
        self._draw_items(d, self._ctx_labels(), self.header_h + 44, self.ctx_sel, 0)

    def _draw_nodeinfo(self, d) -> None:
        title = t("nodeinfo.title", title=self.info_title) if self.info_title else t("nodeinfo.title_default")
        self.fonts.draw(d, (12, self.header_h + 8), title[:42], COL_ACCENT, "normal")
        rows = self._info_rows()
        self.scroll = min(self._info_max_scroll(), self.scroll)
        y = self.header_h + 36
        for i, line in enumerate(self.info_lines[self.scroll:self.scroll + rows]):
            self.fonts.draw(d, (12, y + i * 20), line[:58], COL_TEXT, "small")

    def _draw_list(self, d, caption, items) -> None:
        self.fonts.draw(d, (12, self.header_h + 8), caption, COL_ACCENT, "normal")
        if not items:
            self.fonts.draw(d, (12, self.header_h + 40), t("common.empty"), COL_DIM, "small")
            return
        self._draw_items(d, items, self.header_h + 40, self.sel, self.scroll)

    def _draw_kbd(self, d) -> None:
        kind, idx = self.kbd_target
        if kind == "channel":
            dest = t("kbd.dest_channel", idx=idx)
        elif kind == "dm":
            dest = t("kbd.dest_dm", short=self.radio.name_for_num(idx))
        elif kind == "chname":
            dest = t("kbd.dest_chname")
        elif kind == "filter":
            dest = t("kbd.dest_filter")
        elif kind == "psk":
            dest = t("kbd.dest_psk")
        elif kind == "setting":
            sf = self.settings_fields[idx] if 0 <= idx < len(self.settings_fields) else None
            dest = sf.label if sf else "setting"
        else:
            dest = "?"
        if self.kbd_reply_label:
            dest = f"{self.kbd_reply_label} | {dest}"
        layer_name = KBD_LAYERS[self.kbd_layer][0]
        shift = "ABC" if self.kbd_shift else "abc"
        self.fonts.draw(d, (12, self.header_h + 4),
                        f"{dest} [{layer_name}/{shift}]:", COL_ACCENT, "small")

        if kind in ("channel", "dm"):
            used = self._kbd_used_bytes()
            counter = f"{used}/{MAX_MSG_BYTES}"
            cw = self.fonts.length(counter, "small")
            ccol = COL_ERR if used >= MAX_MSG_BYTES else (
                COL_ACCENT2 if used >= MAX_MSG_BYTES - 20 else COL_DIM)
            self.fonts.draw(d, (self.W - 12 - cw, self.header_h + 4), counter, ccol, "small")

        box_y = self.header_h + 24
        d.rectangle([10, box_y, self.W - 10, box_y + 40], fill=COL_PANEL, outline=COL_ACCENT2)
        shown = self.kbd_text + "_"
        for line in self._wrap(shown, self.W - 28)[-2:]:
            self.fonts.draw(d, (16, box_y + 4), line, COL_TEXT, "small")
            box_y += 18

        grid = self._kbd_grid()
        top = self.header_h + 96
        cell_w = 52
        cell_h = 34
        for r, row in enumerate(grid):
            row_w = len(row) * cell_w
            x0 = max(8, (self.W - row_w) // 2)
            for c, ch in enumerate(row):
                cx = x0 + c * cell_w
                cy = top + r * (cell_h + 6)
                selected = (r == self.kbd_row and c == self.kbd_col)
                kbox = (cx, cy, cx + cell_w - 6, cy + cell_h)
                draw_list_item(d, kbox, selected)
                glyph = ch.upper() if self.kbd_shift else ch
                self.fonts.draw(d, (cx + 18, cy + 6), glyph, COL_TEXT, "normal")

    def _draw_menu(self, d) -> None:
        n = len(self.menu_items)
        y0 = self.header_h + 2
        title_y = y0 + 6
        items_top = y0 + 28
        # Clamp the frame so the last item (Quit) stays above the footer bar.
        max_bottom = self.H - self.footer_h - 4
        bottom = min(max_bottom, items_top + n * self.row_h + 4)
        overlay = (60, y0, self.W - 60, bottom)
        draw_menu_frame(d, overlay)
        self.fonts.draw(d, (overlay[0] + 14, title_y), t("menu.title"), COL_ACCENT2, "normal")
        self._draw_items(d, self.menu_items, items_top, self.menu_sel, 0,
                         x=overlay[0] + 8, width=self.W - 2 * (overlay[0] + 8))

    def _draw_items(self, d, items, top, selected, scroll, x=10, width=None) -> None:
        if width is None:
            width = self.W - 20
        visible = self._list_rows()
        for i in range(scroll, min(len(items), scroll + visible)):
            ry = top + (i - scroll) * self.row_h
            box = (x, ry, x + width, ry + self.row_h - 4)
            draw_list_item(d, box, i == selected)
            mark = "▸ " if i == selected else "  "
            self.fonts.draw(d, (x + 8, ry + 4), (mark + str(items[i]))[:70], COL_TEXT, "normal")

    def _wrap(self, text: str, max_w: int) -> List[str]:
        words = text.split()
        if not words:
            return [""]
        lines: List[str] = []
        cur = words[0]
        for w in words[1:]:
            trial = cur + " " + w
            if self.fonts.length(trial, "small") <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines


def run_fbui(radio: RadioManager, port_dir: Path, log=print) -> int:
    import os
    import sys
    import threading

    from .logutil import log_exception, release_pidfile, write as boot_write

    log("run_fbui: loading SDL")
    try:
        from .sdl2 import SdlScreen
    except Exception:  # noqa: BLE001
        log_exception("SDL import failed")
        return 1

    set_language(load_language(port_dir))
    presets = load_presets(port_dir)
    log("run_fbui: creating screen")
    try:
        screen = SdlScreen(640, 480, log=log)
    except Exception:  # noqa: BLE001
        log_exception("SdlScreen init failed")
        return 1

    from .audio import SfxPlayer
    from .fonts import Fonts
    from .splash import play_radar_splash

    sfx = SfxPlayer(log=log, port_dir=port_dir)
    sfx.modem_connect()

    fonts: Optional[Fonts] = None
    try:
        fonts = Fonts()
        play_radar_splash(
            screen,
            fonts,
            unfold=True,
            duration=1.8,
            phase_text="INITIALIZING UPLINK",
            pump=screen.pump,
        )
    except Exception:  # noqa: BLE001
        log_exception("boot splash failed")

    actions: "queue.Queue[str]" = queue.Queue()

    from .inputs import InputReader

    reader = InputReader(actions, log=log, port_dir=port_dir)
    reader.start()
    log("run_fbui: input reader started")

    exit_code = 0
    try:
        log("run_fbui: creating FbUI")
        ui = FbUI(screen, radio, presets, port_dir, log=log, sfx=sfx)
        log("run_fbui: entering main loop")
        ui.run(actions, reader=reader)
        log("run_fbui: main loop ended")
    except Exception:  # noqa: BLE001
        exit_code = 1
        log_exception("GUI crashed")
    finally:
        boot_write("run_fbui: shutdown begin")
        reader.stop()
        sfx.modem_disconnect()
        try:
            if fonts is not None:
                play_radar_splash(
                    screen,
                    fonts,
                    unfold=False,
                    duration=1.4,
                    phase_text="LINK DOWN",
                    pump=screen.pump,
                )
        except Exception:  # noqa: BLE001
            log_exception("shutdown splash failed")
        try:
            sfx.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            screen.close()
        except Exception:  # noqa: BLE001
            pass

        done = threading.Event()

        def _disc():
            try:
                radio.disconnect()
            except Exception:  # noqa: BLE001
                log_exception("disconnect error")
            finally:
                done.set()

        threading.Thread(target=_disc, daemon=True).start()
        if not done.wait(1.0):
            boot_write("BLE disconnect slow — forcing exit")
        release_pidfile(port_dir)
        boot_write(f"exiting code={exit_code}")
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(exit_code)
