"""兼容导出层：保留旧 ws.py 入口，实际实现分散在 ws_* 模块。"""

from __future__ import annotations

from .ws_bridge import forward_onebot_message
from .ws_connection import (
    _close_ws_connection,
    _connect_ws,
    _is_ws_connected,
    _logout_current_bot,
)
from .ws_processor import (
    _delegate_to_ws,
    _execute_git_pull,
    _format_status,
    dispatch_command,
)

__all__ = [
    "_close_ws_connection",
    "_connect_ws",
    "_delegate_to_ws",
    "_execute_git_pull",
    "_format_status",
    "_is_ws_connected",
    "_logout_current_bot",
    "dispatch_command",
    "forward_onebot_message",
]
