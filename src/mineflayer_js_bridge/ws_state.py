"""WebSocket 桥接运行时状态。

其他模块必须通过 `ws_state.xxx` 读写这些变量，避免导入变量副本后赋值失效。
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment

active_bot: Bot | None = None
active_event: Event | None = None
ws_connection: Any | None = None
ws_reader_task: asyncio.Task[None] | None = None
player_poll_task: asyncio.Task[None] | None = None
authenticated = False
current_bot_id: str | None = None
current_bot_state = "offline"
pending_replies: dict[str, asyncio.Future[dict[str, Any]]] = {}
connection_lock = asyncio.Lock()
pending_bridge_messages: deque[str | Message | MessageSegment] = deque(maxlen=50)
