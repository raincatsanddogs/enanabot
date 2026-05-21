"""WebSocket 请求、回复和读取循环。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nonebot import logger
from websockets.exceptions import ConnectionClosed

from . import ws_state
from .context import config
from .utils import new_msg_id, now_ms


async def _send_payload(payload: dict[str, Any]) -> None:
    if ws_state.ws_connection is None:
        msg = "WebSocket 未连接"
        raise RuntimeError(msg)
    await ws_state.ws_connection.send(json.dumps(payload, ensure_ascii=False))


async def _send_request(
    message_type: str,
    data: dict[str, Any] | None = None,
    *,
    bot_id: str | None = None,
    extra: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    msg_id = new_msg_id(message_type)
    payload: dict[str, Any] = {
        "type": message_type,
        "timestamp": now_ms(),
        "need_reply": True,
        "msg_id": msg_id,
        "data": data or {},
        "extra": extra or {},
    }
    if bot_id:
        payload["bot_id"] = bot_id

    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    ws_state.pending_replies[msg_id] = future
    try:
        await _send_payload(payload)
        reply = await asyncio.wait_for(
            future,
            timeout=timeout or config.mineflayer_ws_request_timeout,
        )
    finally:
        ws_state.pending_replies.pop(msg_id, None)

    reply_data = reply.get("data", {})
    if not isinstance(reply_data, dict):
        msg = "WebSocket 回复格式无效"
        raise RuntimeError(msg)

    status = reply_data.get("status")
    result = reply_data.get("result")
    if status == "error":
        if isinstance(result, dict):
            error_message = result.get("error_message") or result.get("error_type")
            raise RuntimeError(str(error_message or result))
        raise RuntimeError(str(result or "请求失败"))
    return reply


async def _send_reply(
    msg_id: str,
    result: Any = None,
    *,
    status: str = "success",
) -> None:
    payload = {
        "type": "reply",
        "timestamp": now_ms(),
        "need_reply": False,
        "data": {
            "msg_id": msg_id,
            "status": status,
            "result": result,
        },
    }
    await _send_payload(payload)


async def _read_ws_messages() -> None:
    try:
        while ws_state.ws_connection is not None:
            raw_message = await ws_state.ws_connection.recv()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                logger.warning(f"收到非 JSON WebSocket 消息: {raw_message}")
                continue

            if not isinstance(message, dict):
                logger.warning(f"收到无效 WebSocket 消息: {message}")
                continue

            from .ws_processor import _handle_ws_message

            await _handle_ws_message(message)
    except asyncio.CancelledError:
        raise
    except ConnectionClosed as error:
        logger.warning(f"Mineflayer WebSocket 连接关闭: {error}")
    except Exception as error:
        logger.exception(f"Mineflayer WebSocket 读取失败: {error}")
    finally:
        if ws_state.ws_connection is not None:
            ws_state.current_bot_state = "offline"
            # 延迟导入，避免 transport 与 connection 在模块加载期互相依赖。
            from .ws_connection import _close_ws_connection

            await _close_ws_connection(persist_state=False)
