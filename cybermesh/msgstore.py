"""Persist chat history to SD card (survives app restarts)."""

from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Deque, Dict, Optional

from .chat_types import MAX_MSGS, ChatMessage, SEND_NONE, SEND_PENDING, SEND_SENT

_STORE_VERSION = 1


def _history_path(port_dir: Path) -> Path:
    return port_dir / "messages.json"


def msg_to_dict(msg: ChatMessage) -> dict:
    return {
        "text": msg.text,
        "sender": msg.sender,
        "ts": msg.ts,
        "sender_num": msg.sender_num,
        "channel": msg.channel,
        "is_dm": msg.is_dm,
        "peer_num": msg.peer_num,
        "msg_id": msg.msg_id,
        "reply_id": msg.reply_id,
        "from_me": msg.from_me,
        "send_status": msg.send_status,
        "send_error": msg.send_error,
    }


def msg_from_dict(d: dict) -> ChatMessage:
    send_status = str(d.get("send_status", "none"))
    if send_status in (SEND_SENT, SEND_PENDING):
        send_status = SEND_NONE
    return ChatMessage(
        text=str(d.get("text", "")),
        sender=str(d.get("sender", "?")),
        ts=float(d.get("ts", 0)),
        sender_num=d.get("sender_num"),
        channel=int(d.get("channel", 0)),
        is_dm=bool(d.get("is_dm", False)),
        peer_num=d.get("peer_num"),
        msg_id=d.get("msg_id"),
        reply_id=d.get("reply_id"),
        from_me=bool(d.get("from_me", False)),
        send_status=send_status,
        send_error=d.get("send_error"),
    )


def load_history(
    port_dir: Path,
) -> tuple[Dict[int, Deque[ChatMessage]], Dict[int, Deque[ChatMessage]]]:
    path = _history_path(port_dir)
    channel_msgs: Dict[int, Deque[ChatMessage]] = {}
    dm_msgs: Dict[int, Deque[ChatMessage]] = {}
    if not path.exists():
        return channel_msgs, dm_msgs
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _STORE_VERSION:
            return channel_msgs, dm_msgs
        for ch_str, items in (data.get("channel") or {}).items():
            ch = int(ch_str)
            dq: Deque[ChatMessage] = deque(maxlen=MAX_MSGS)
            for item in items[-MAX_MSGS:]:
                dq.append(msg_from_dict(item))
            if dq:
                channel_msgs[ch] = dq
        for peer_str, items in (data.get("dm") or {}).items():
            peer = int(peer_str)
            dq = deque(maxlen=MAX_MSGS)
            for item in items[-MAX_MSGS:]:
                dq.append(msg_from_dict(item))
            if dq:
                dm_msgs[peer] = dq
    except Exception:  # noqa: BLE001
        pass
    return channel_msgs, dm_msgs


class HistoryStore:
    """Debounced writer so exFAT isn't hammered on every packet."""

    def __init__(self, port_dir: Path, log=None) -> None:
        self.port_dir = port_dir
        self.log = log or (lambda _m: None)
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._pending = False

    def schedule_save(
        self,
        channel_msgs: Dict[int, Deque[ChatMessage]],
        dm_msgs: Dict[int, Deque[ChatMessage]],
        device_addr: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._pending = True
            self._channel_msgs = channel_msgs
            self._dm_msgs = dm_msgs
            self._device_addr = device_addr
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(0.4, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def flush_now(
        self,
        channel_msgs: Dict[int, Deque[ChatMessage]],
        dm_msgs: Dict[int, Deque[ChatMessage]],
        device_addr: Optional[str] = None,
    ) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = False
        self._write(channel_msgs, dm_msgs, device_addr)

    def _flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            ch = self._channel_msgs
            dm = self._dm_msgs
            addr = self._device_addr
            self._pending = False
            self._timer = None
        self._write(ch, dm, addr)

    def _write(
        self,
        channel_msgs: Dict[int, Deque[ChatMessage]],
        dm_msgs: Dict[int, Deque[ChatMessage]],
        device_addr: Optional[str],
    ) -> None:
        path = _history_path(self.port_dir)
        try:
            payload = {
                "version": _STORE_VERSION,
                "device": device_addr or "",
                "channel": {
                    str(ch): [msg_to_dict(m) for m in msgs]
                    for ch, msgs in channel_msgs.items()
                },
                "dm": {
                    str(peer): [msg_to_dict(m) for m in msgs]
                    for peer, msgs in dm_msgs.items()
                },
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:  # noqa: BLE001
            self.log(f"history save failed: {exc}")
