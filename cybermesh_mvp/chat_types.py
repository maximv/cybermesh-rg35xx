"""Shared chat message types."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

MAX_MSGS = 50
# none = incoming / legacy; pending | sent | failed = outbound
SEND_NONE = "none"
SEND_PENDING = "pending"
SEND_SENT = "sent"
SEND_FAILED = "failed"


@dataclass
class ChatMessage:
    text: str
    sender: str
    ts: float = field(default_factory=time.time)
    sender_num: Optional[int] = None
    channel: int = 0
    is_dm: bool = False
    peer_num: Optional[int] = None
    msg_id: Optional[int] = None
    reply_id: Optional[int] = None
    from_me: bool = False
    send_status: str = SEND_NONE
    send_error: Optional[str] = None
