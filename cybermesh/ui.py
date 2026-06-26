"""Curses UI for small handheld screens."""

from __future__ import annotations

import curses
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .radio import BleDevice, ChatMessage, RadioManager
from .theme import APP_NAME


def load_presets(port_dir: Path) -> List[str]:
    path = port_dir / "presets.txt"
    if not path.exists():
        return ["На связи", "OK"]
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    return [ln for ln in lines if ln] or ["OK"]


class CybermeshApp:
    def __init__(self, stdscr, radio: RadioManager, presets: List[str]) -> None:
        self.stdscr = stdscr
        self.radio = radio
        self.presets = presets
        self.screen = "scan"
        self.selected = 0
        self.scroll = 0
        self.status = ""
        self.status_until = 0.0
        self.menu_items = ["Send", "Nodes", "Rescan", "Disconnect", "Quit"]
        self.menu_selected = 0
        self.in_menu = False

    def set_status(self, text: str, seconds: float = 2.5) -> None:
        self.status = text
        self.status_until = time.time() + seconds

    def run(self) -> None:
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)
        self.radio.start_scan()

        while True:
            self._drain_events()
            self._draw()
            self._handle_input()
            time.sleep(0.03)

    def _drain_events(self) -> None:
        while True:
            try:
                self.radio.events.get_nowait()
            except Exception:
                break

    def _handle_input(self) -> None:
        try:
            ch = self.stdscr.getch()
        except curses.error:
            ch = -1
        if ch == -1:
            return

        if ch in (3, 26):  # Ctrl+C / Ctrl+Z
            self.radio.disconnect()
            raise SystemExit

        if self.in_menu:
            self._input_menu(ch)
            return

        snap = self.radio.snapshot()
        state, devices = snap.state, snap.devices

        if self.screen == "scan":
            self._input_scan(ch, state, devices)
        elif self.screen == "chat":
            self._input_chat(ch)
        elif self.screen == "nodes":
            self._input_nodes(ch)
        elif self.screen == "send":
            self._input_send(ch)

    def _input_menu(self, ch: int) -> None:
        if ch in (curses.KEY_UP, ord("k")):
            self.menu_selected = max(0, self.menu_selected - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.menu_selected = min(len(self.menu_items) - 1, self.menu_selected + 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            self._activate_menu()
        elif ch in (27, ord("b")):
            self.in_menu = False

    def _activate_menu(self) -> None:
        choice = self.menu_items[self.menu_selected]
        self.in_menu = False
        if choice == "Send":
            self.screen = "send"
            self.selected = 0
        elif choice == "Nodes":
            self.screen = "nodes"
            self.selected = 0
            self.scroll = 0
        elif choice == "Rescan":
            self.radio.disconnect()
            self.screen = "scan"
            self.selected = 0
            self.radio.start_scan()
        elif choice == "Disconnect":
            self.radio.disconnect()
            self.screen = "scan"
            self.selected = 0
            self.radio.start_scan()
        elif choice == "Quit":
            self.radio.disconnect()
            raise SystemExit

    def _input_scan(self, ch: int, state: str, devices: List[BleDevice]) -> None:
        if state == "scanning":
            if ch in (27, ord("r")):
                self.radio.start_scan()
            return

        if ch in (curses.KEY_UP, ord("k")):
            self.selected = max(0, self.selected - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.selected = min(max(0, len(devices) - 1), self.selected + 1)
        elif ch in (10, 13, curses.KEY_ENTER) and devices:
            self.radio.connect(devices[self.selected].address)
            self.screen = "chat"
            self.scroll = 0
        elif ch in (ord("r"),):
            self.radio.start_scan()
        elif ch in (ord("q"), 27):
            raise SystemExit

    def _input_chat(self, ch: int) -> None:
        snap = self.radio.snapshot()
        state = snap.state
        messages = self.radio.channel_messages(0)
        if state != "connected":
            if state == "error":
                self.set_status(self.radio.error or "Error")
            self.screen = "scan"
            self.radio.start_scan()
            return

        max_scroll = max(0, len(messages) - self._chat_visible_lines())
        if ch in (curses.KEY_UP, ord("k")):
            self.scroll = max(0, self.scroll - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.scroll = min(max_scroll, self.scroll + 1)
        elif ch in (ord("s"), ord("x")):
            self.screen = "send"
            self.selected = 0
        elif ch in (ord("n"), ord("y")):
            self.screen = "nodes"
            self.selected = 0
            self.scroll = 0
        elif ch in (ord("m"),):
            self.in_menu = True
            self.menu_selected = 0
        elif ch in (27, ord("b")):
            self.in_menu = True
        elif ch in (ord("r"),):
            self.radio.start_scan()
            self.screen = "scan"

    def _input_nodes(self, ch: int) -> None:
        rows = self.radio.node_rows()
        if ch in (curses.KEY_UP, ord("k")):
            self.selected = max(0, self.selected - 1)
            if self.selected < self.scroll:
                self.scroll = self.selected
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.selected = min(max(0, len(rows) - 1), self.selected + 1)
            visible = max(1, curses.LINES - 6)
            if self.selected >= self.scroll + visible:
                self.scroll = self.selected - visible + 1
        elif ch in (27, ord("b"), 10, 13, curses.KEY_ENTER):
            self.screen = "chat"

    def _input_send(self, ch: int) -> None:
        if ch in (curses.KEY_UP, ord("k")):
            self.selected = max(0, self.selected - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.selected = min(len(self.presets) - 1, self.selected + 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            text = self.presets[self.selected]
            err = self.radio.send_text(text)
            if err:
                self.set_status(err, 4)
            else:
                self.set_status(f"Sent: {text[:24]}")
            self.screen = "chat"
        elif ch in (27, ord("b")):
            self.screen = "chat"

    def _chat_visible_lines(self) -> int:
        return max(3, curses.LINES - 5)

    def _draw(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        snap = self.radio.snapshot()
        state, error, devices = snap.state, snap.error, snap.devices
        messages = self.radio.channel_messages(0)

        title = APP_NAME
        if state == "connected":
            title += " [BLE]"
        elif state == "connecting":
            title += " [connecting...]"
        self.stdscr.addstr(0, 0, title[: w - 1], curses.A_BOLD)

        if self.in_menu:
            self._draw_menu()
        elif self.screen == "scan":
            self._draw_scan(state, error, devices)
        elif self.screen == "chat":
            self._draw_chat(state, messages)
        elif self.screen == "nodes":
            self._draw_nodes()
        elif self.screen == "send":
            self._draw_send()

        footer = "A:OK B:Menu X:Send Y:Nodes START:Menu"
        if time.time() < self.status_until and self.status:
            footer = self.status[: w - 1]
        try:
            self.stdscr.addstr(h - 1, 0, footer[: w - 1], curses.A_REVERSE)
        except curses.error:
            pass
        self.stdscr.refresh()

    def _draw_scan(self, state: str, error: Optional[str], devices: List[BleDevice]) -> None:
        lines = []
        if state == "scanning":
            lines.append("Scanning BLE...")
        elif state == "connecting":
            lines.append("Connecting...")
        elif state == "error":
            lines.append("Error:")
            lines.append(error or "?")
            lines.append("")
            lines.append("Pair via bluetoothctl, then R to rescan.")
        elif state == "no_devices":
            lines.append("BLE-узлы не найдены.")
            lines.append("Enable BT on the radio. R = rescan.")
        else:
            lines.append("Select device (Enter connect):")
            for idx, dev in enumerate(devices):
                mark = ">" if idx == self.selected else " "
                lines.append(f"{mark} {dev.name} {dev.address}")

        self._draw_body(lines)

    def _draw_chat(self, state: str, messages: List[ChatMessage]) -> None:
        if state == "connecting":
            self._draw_body(["Connecting over BLE..."])
            return
        visible = self._chat_visible_lines()
        start = max(0, len(messages) - visible - self.scroll)
        end = len(messages) - self.scroll
        chunk = messages[start:end]

        lines = ["Channel 0", ""]
        for msg in chunk:
            who = "You" if msg.sender == "me" else msg.sender
            for part in self._wrap(f"{who}: {msg.text}"):
                lines.append(part)
        if not chunk:
            lines.append("(no messages yet)")
        self._draw_body(lines)

    def _draw_nodes(self) -> None:
        rows = self.radio.node_rows()
        lines = ["Nodes", ""]
        if not rows:
            lines.append("(waiting for node DB...)")
        else:
            visible = max(1, curses.LINES - 6)
            for idx in range(self.scroll, min(len(rows), self.scroll + visible)):
                mark = ">" if idx == self.selected else " "
                lines.append(f"{mark} {rows[idx]}")
        self._draw_body(lines)

    def _draw_send(self) -> None:
        lines = ["Send preset:", ""]
        for idx, preset in enumerate(self.presets):
            mark = ">" if idx == self.selected else " "
            lines.append(f"{mark} {preset}")
        lines.append("")
        lines.append("Edit presets.txt over SSH for more.")
        self._draw_body(lines)

    def _draw_menu(self) -> None:
        lines = ["Menu", ""]
        for idx, item in enumerate(self.menu_items):
            mark = ">" if idx == self.menu_selected else " "
            lines.append(f"{mark} {item}")
        self._draw_body(lines)

    def _draw_body(self, lines: List[str]) -> None:
        h, w = self.stdscr.getmaxyx()
        row = 2
        for line in lines:
            if row >= h - 1:
                break
            try:
                self.stdscr.addstr(row, 0, line[: w - 1])
            except curses.error:
                pass
            row += 1

    @staticmethod
    def _wrap(text: str, width: int = 38) -> List[str]:
        words = text.split()
        if not words:
            return [""]
        lines: List[str] = []
        current = words[0]
        for word in words[1:]:
            if len(current) + 1 + len(word) <= width:
                current += " " + word
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines


def run_ui(stdscr, radio: RadioManager, port_dir: Path) -> None:
    presets = load_presets(port_dir)
    app = CybermeshApp(stdscr, radio, presets)
    app.run()
