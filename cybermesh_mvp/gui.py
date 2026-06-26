"""Pygame GUI for 640x480 handheld screens."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Tuple

import pygame

from .display import init_display
from .radio import BleDevice, ChatMessage, RadioManager

# 640x480 — RG35xx PRO
WIDTH, HEIGHT = 640, 480
HEADER_H = 36
FOOTER_H = 32
ROW_H = 28

from .theme import (
    COL_ACCENT,
    COL_BG,
    COL_DIM,
    COL_ERR,
    COL_ME,
    COL_PANEL,
    COL_SEL,
    COL_TEXT,
    APP_NAME,
)

# pygame uses RGB tuples without alpha
COL_BG = COL_BG[:3]
COL_PANEL = COL_PANEL[:3]
COL_ACCENT = COL_ACCENT[:3]
COL_TEXT = COL_TEXT[:3]
COL_DIM = COL_DIM[:3]
COL_SEL = COL_SEL[:3]
COL_ERR = COL_ERR[:3]
COL_ME = COL_ME[:3]


def load_presets(port_dir: Path) -> List[str]:
    path = port_dir / "presets.txt"
    if not path.exists():
        return ["На связи", "OK"]
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    return [ln for ln in lines if ln] or ["OK"]


class CybermeshGui:
    def __init__(self, screen: pygame.Surface, radio: RadioManager, presets: List[str]) -> None:
        self.screen = screen
        self.radio = radio
        self.presets = presets
        self.screen_id = "scan"
        self.selected = 0
        self.scroll = 0
        self.status = ""
        self.status_until = 0.0
        self.menu_open = False
        self.menu_items = ["Send", "Nodes", "Rescan", "Disconnect", "Quit"]
        self.menu_sel = 0
        self.joy: Optional[pygame.joystick.Joystick] = None
        self._repeat_at = 0.0
        self._last_nav = 0

        pygame.font.init()
        self.font = pygame.font.SysFont("dejavusans,dejavusansmono,liberationsans,arial", 18)
        self.font_sm = pygame.font.SysFont("dejavusans,dejavusansmono,liberationsans,arial", 15)
        self.font_lg = pygame.font.SysFont("dejavusans,dejavusansmono,liberationsans,arial", 22, bold=True)

        self._init_joystick()
        self.radio.start_scan()

    def _init_joystick(self) -> None:
        pygame.joystick.init()
        if pygame.joystick.get_count() > 0:
            self.joy = pygame.joystick.Joystick(0)
            self.joy.init()

    def set_status(self, text: str, seconds: float = 2.5) -> None:
        self.status = text
        self.status_until = time.time() + seconds

    def run(self) -> None:
        clock = pygame.time.Clock()
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if not self._on_key(event.key):
                        running = False
                elif event.type == pygame.JOYBUTTONDOWN:
                    if not self._on_joy_button(event.button):
                        running = False

            self._poll_axis()
            self._drain_radio()
            self._draw()
            clock.tick(30)

        self.radio.disconnect()
        pygame.quit()

    def _drain_radio(self) -> None:
        while True:
            try:
                self.radio.events.get_nowait()
            except Exception:
                break
        snap = self.radio.snapshot()
        if self.screen_id == "chat" and snap.state in ("error", "disconnected"):
            self.screen_id = "scan"
            self.radio.start_scan()

    def _nav_cooldown(self) -> bool:
        now = time.time()
        if now - self._last_nav < 0.14:
            return False
        self._last_nav = now
        return True

    def _poll_axis(self) -> None:
        if self.joy is None:
            return
        now = time.time()
        if now < self._repeat_at:
            return
        hat = self.joy.get_hat(0) if self.joy.get_numhats() > 0 else (0, 0)
        dy = -hat[1]
        dx = hat[0]
        if self.joy.get_numaxes() >= 2:
            ax = self.joy.get_axis(0)
            ay = self.joy.get_axis(1)
            if abs(ax) > 0.45:
                dx = 1 if ax > 0 else -1
            if abs(ay) > 0.45:
                dy = 1 if ay > 0 else -1
        if dy < 0:
            self._move_sel(-1)
            self._repeat_at = now + 0.12
        elif dy > 0:
            self._move_sel(1)
            self._repeat_at = now + 0.12

    def _move_sel(self, delta: int) -> None:
        if delta == 0:
            return
        if not self._nav_cooldown():
            return
        snap = self.radio.snapshot()
        state = snap.state
        devices = snap.devices
        messages = self.radio.channel_messages(0)
        if self.menu_open:
            self.menu_sel = max(0, min(len(self.menu_items) - 1, self.menu_sel + delta))
            return
        if self.screen_id == "scan" and state not in ("scanning", "connecting"):
            self.selected = max(0, min(max(0, len(devices) - 1), self.selected + delta))
        elif self.screen_id == "send":
            self.selected = max(0, min(len(self.presets) - 1, self.selected + delta))
        elif self.screen_id == "nodes":
            rows = self.radio.node_rows()
            self.selected = max(0, min(max(0, len(rows) - 1), self.selected + delta))
            self._sync_scroll(len(rows))
        elif self.screen_id == "chat":
            max_scroll = max(0, len(messages) - self._chat_rows())
            self.scroll = max(0, min(max_scroll, self.scroll + delta))

    def _sync_scroll(self, total: int) -> None:
        visible = self._list_rows()
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + visible:
            self.scroll = self.selected - visible + 1
        if total <= visible:
            self.scroll = 0

    def _chat_rows(self) -> int:
        return max(4, (HEIGHT - HEADER_H - FOOTER_H - 16) // 22)

    def _list_rows(self) -> int:
        return max(3, (HEIGHT - HEADER_H - FOOTER_H - 24) // ROW_H)

    def _on_key(self, key: int) -> bool:
        if key in (pygame.K_ESCAPE, pygame.K_b):
            return self._back()
        if key in (pygame.K_UP, pygame.K_k):
            self._move_sel(-1)
        elif key in (pygame.K_DOWN, pygame.K_j):
            self._move_sel(1)
        elif key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_a):
            return self._activate()
        elif key == pygame.K_s:
            if self.screen_id == "chat":
                self.screen_id = "send"
                self.selected = 0
        elif key == pygame.K_n:
            if self.screen_id == "chat":
                self.screen_id = "nodes"
                self.selected = 0
                self.scroll = 0
        elif key == pygame.K_m:
            self.menu_open = not self.menu_open
        elif key == pygame.K_r:
            self.radio.start_scan()
            self.screen_id = "scan"
        return True

    def _on_joy_button(self, button: int) -> bool:
        # Common RG35xx: 0=A, 1=B, 2=X, 3=Y, 6=Start, 4=Select
        if button == 0:
            return self._activate()
        if button == 1:
            return self._back()
        if button == 2 and self.screen_id == "chat":
            self.screen_id = "send"
            self.selected = 0
            return True
        if button == 3 and self.screen_id == "chat":
            self.screen_id = "nodes"
            self.selected = 0
            self.scroll = 0
            return True
        if button in (6, 7):
            self.menu_open = not self.menu_open
            return True
        if button == 4:
            self.radio.start_scan()
            self.screen_id = "scan"
            return True
        return True

    def _back(self) -> bool:
        if self.menu_open:
            self.menu_open = False
            return True
        if self.screen_id in ("send", "nodes"):
            self.screen_id = "chat"
            return True
        if self.screen_id == "chat":
            self.menu_open = True
            return True
        return False

    def _activate(self) -> bool:
        if self.menu_open:
            item = self.menu_items[self.menu_sel]
            self.menu_open = False
            if item == "Send":
                self.screen_id = "send"
                self.selected = 0
            elif item == "Nodes":
                self.screen_id = "nodes"
                self.selected = 0
                self.scroll = 0
            elif item == "Rescan":
                self.radio.disconnect()
                self.screen_id = "scan"
                self.selected = 0
                self.radio.start_scan()
            elif item == "Disconnect":
                self.radio.disconnect()
                self.screen_id = "scan"
                self.selected = 0
                self.radio.start_scan()
            elif item == "Quit":
                return False
            return True

        snap = self.radio.snapshot()
        state = snap.state
        devices = snap.devices
        if self.screen_id == "scan" and devices and state not in ("scanning", "connecting"):
            self.radio.connect(devices[self.selected].address)
            self.screen_id = "chat"
            self.scroll = 0
        elif self.screen_id == "send":
            text = self.presets[self.selected]
            err = self.radio.send_text(text)
            if err:
                self.set_status(err, 4)
            else:
                self.set_status(f"Sent: {text[:30]}")
            self.screen_id = "chat"
        return True

    def _draw(self) -> None:
        self.screen.fill(COL_BG)
        snap = self.radio.snapshot()
        state, error, devices = snap.state, snap.error, snap.devices
        messages = self.radio.channel_messages(0)
        title = APP_NAME
        if state == "connected":
            title += "  BLE"
        elif state == "connecting":
            title += "  ..."
        self._header(title)

        if self.menu_open:
            self._draw_menu()
        elif self.screen_id == "scan":
            self._draw_scan(state, error, devices)
        elif self.screen_id == "chat":
            self._draw_chat(messages, state)
        elif self.screen_id == "send":
            self._draw_list("Send message", self.presets)
        elif self.screen_id == "nodes":
            self._draw_list("Nodes", self.radio.node_rows())

        self._footer()
        pygame.display.flip()

    def _header(self, title: str) -> None:
        pygame.draw.rect(self.screen, COL_PANEL, (0, 0, WIDTH, HEADER_H))
        surf = self.font_lg.render(title, True, COL_ACCENT)
        self.screen.blit(surf, (12, 6))

    def _footer(self) -> None:
        y = HEIGHT - FOOTER_H
        pygame.draw.rect(self.screen, COL_PANEL, (0, y, WIDTH, FOOTER_H))
        if time.time() < self.status_until and self.status:
            text = self.status
        else:
            text = "A:OK  B:Back  X:Send  Y:Nodes  Start:Menu"
        surf = self.font_sm.render(text[:90], True, COL_DIM)
        self.screen.blit(surf, (8, y + 8))

    def _draw_scan(self, state: str, error: Optional[str], devices: List[BleDevice]) -> None:
        y = HEADER_H + 8
        if state == "scanning":
            self._label("Scanning Bluetooth...", y)
            return
        if state == "connecting":
            self._label("Connecting...", y)
            return
        if state == "error":
            self._label("Error:", y)
            self._label(error or "?", y + 26, COL_ERR)
            self._label("Pair in bluetoothctl, then Rescan (Select)", y + 56, COL_DIM)
            return
        if state == "no_devices":
            self._label("BLE-узлы не найдены.", y)
            self._label("Enable BT on Heltec. Select = rescan.", y + 26, COL_DIM)
            return
        self._label("Select radio:", y)
        self._draw_list_items(
            [f"{d.name}  {d.address}" for d in devices],
            HEADER_H + 36,
            self.selected,
            self.scroll,
        )

    def _draw_chat(self, messages: List[ChatMessage], state: str) -> None:
        if state == "connecting":
            self._label("Connecting over BLE...", HEADER_H + 12)
            return
        rows = self._chat_rows()
        start = max(0, len(messages) - rows - self.scroll)
        chunk = messages[start : len(messages) - self.scroll]
        y = HEADER_H + 6
        self._label("Channel 0", y, COL_ACCENT)
        y += 24
        if not chunk:
            self._label("(no messages yet)", y, COL_DIM)
            return
        for msg in chunk:
            who = "You" if msg.sender == "me" else msg.sender
            color = COL_ME if msg.sender == "me" else COL_TEXT
            for line in self._wrap(f"{who}: {msg.text}", 54):
                if y > HEIGHT - FOOTER_H - 20:
                    break
                self._label(line, y, color)
                y += 20

    def _draw_menu(self) -> None:
        overlay = pygame.Surface((WIDTH, HEIGHT - HEADER_H - FOOTER_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, HEADER_H))
        box = pygame.Rect(80, 100, WIDTH - 160, 280)
        pygame.draw.rect(self.screen, COL_PANEL, box, border_radius=8)
        pygame.draw.rect(self.screen, COL_ACCENT, box, 2, border_radius=8)
        self._label("Menu", box.x + 16, box.y + 12, COL_ACCENT)
        self._draw_list_items(self.menu_items, box.y + 44, self.menu_sel, 0, box.x + 8, box.width - 16)

    def _draw_list(self, caption: str, items: List[str]) -> None:
        self._label(caption, HEADER_H + 8, COL_ACCENT)
        if not items:
            self._label("(empty)", HEADER_H + 40, COL_DIM)
            return
        self._draw_list_items(items, HEADER_H + 36, self.selected, self.scroll)

    def _draw_list_items(
        self,
        items: List[str],
        top: int,
        selected: int,
        scroll: int,
        x: int = 10,
        width: int = WIDTH - 20,
    ) -> None:
        visible = self._list_rows()
        for i in range(scroll, min(len(items), scroll + visible)):
            row_y = top + (i - scroll) * ROW_H
            rect = pygame.Rect(x, row_y, width, ROW_H - 4)
            if i == selected:
                pygame.draw.rect(self.screen, COL_SEL, rect, border_radius=4)
            else:
                pygame.draw.rect(self.screen, COL_PANEL, rect, border_radius=4)
            mark = ">" if i == selected else " "
            text = f"{mark} {items[i]}"
            surf = self.font.render(text[:64], True, COL_TEXT)
            self.screen.blit(surf, (rect.x + 8, rect.y + 4))

    def _label(self, text: str, y: int, color: Tuple[int, int, int] = COL_TEXT) -> None:
        surf = self.font.render(text[:80], True, color)
        self.screen.blit(surf, (12, y))

    @staticmethod
    def _wrap(text: str, width: int) -> List[str]:
        words = text.split()
        if not words:
            return [""]
        lines: List[str] = []
        cur = words[0]
        for w in words[1:]:
            if len(cur) + 1 + len(w) <= width:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines


def run_gui(radio: RadioManager, port_dir: Path) -> str:
    presets = load_presets(port_dir)
    screen, driver = init_display(WIDTH, HEIGHT)
    app = CybermeshGui(screen, radio, presets)
    app.run()
    return driver
