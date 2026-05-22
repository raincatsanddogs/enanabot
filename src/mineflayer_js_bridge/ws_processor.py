"""处理 Mineflayer WebSocket 服务端推送和本地命令分发。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment

try:
    from src.utils.git_ops import execute_git_pull
    from src.utils.permission import PermissionLevel
except ModuleNotFoundError:
    from utils.git_ops import execute_git_pull
    from utils.permission import PermissionLevel

from . import ws_state
from .context import config
from .utils import (
    dispatch_nonebot_command,
    extract_command_nickname,
    extract_command_user_id,
    fetch_achievement_image,
    format_target,
    load_runtime_state,
    message_result_text,
    parse_positive_int,
    resolve_nonebot_command_target,
    save_runtime_state,
    try_parse_advancement_message,
    try_translate_message,
)
from .ws_bridge import _send_bridge_message
from .ws_connection import (
    _close_ws_connection,
    _connect_ws,
    _is_ws_connected,
    _logout_current_bot,
    _set_current_bot,
)
from .ws_transport import _send_reply, _send_request


async def _handle_ws_message(message: dict[str, Any]) -> None:
    message_type = message.get("type")

    if message_type == "reply":
        data = message.get("data", {})
        msg_id = data.get("msg_id") if isinstance(data, dict) else None
        if isinstance(msg_id, str):
            future = ws_state.pending_replies.get(msg_id)
            if future is not None and not future.done():
                future.set_result(message)
                return
        logger.debug(f"收到未匹配的 WebSocket reply: {message}")
        return

    if message_type == "msg":
        await _handle_mc_message(message)
        return

    if message_type == "event":
        await _handle_server_event(message)
        return

    if message_type == "command":
        await _handle_server_command(message)
        return

    if message_type == "error":
        await _handle_server_error(message)
        return

    logger.warning(f"收到未知 WebSocket type: {message_type}")


async def _handle_mc_message(message: dict[str, Any]) -> None:
    data = message.get("data", {})
    if not isinstance(data, dict):
        return

    if data.get("position") == "private_outgoing":
        return

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        advancement = try_parse_advancement_message(message)
        if advancement is not None:
            if config.mineflayer_enable_mcgen:
                try:
                    image_data = await fetch_achievement_image(
                        config.mineflayer_mcgen_api_url,
                        advancement,
                    )
                    await _send_bridge_message(MessageSegment.image(image_data))
                    return
                except Exception as error:
                    logger.warning(f"mcgen 进度图片渲染失败，已回退纯文本: {error}")
            text = advancement.fallback_text
        else:
            translated = try_translate_message(message)
            if isinstance(translated, str) and translated.strip():
                text = translated
            else:
                return

    await _send_bridge_message(f"{config.mineflayer_ws_mc_prefix}{text}")


async def _handle_server_event(message: dict[str, Any]) -> None:
    data = message.get("data", {})
    if not isinstance(data, dict):
        return

    event_type = data.get("event_type")
    event_data = data.get("event_data", {})
    if event_type == "bot.status" and isinstance(event_data, dict):
        state = event_data.get("state")
        if isinstance(state, str):
            _set_current_bot(message.get("bot_id") or ws_state.current_bot_id, state)
            save_runtime_state(
                should_connect=True,
                mc_bot_id=ws_state.current_bot_id,
                mc_bot_state=ws_state.current_bot_state,
            )
    elif event_type in {"tpa.notification", "system.notice"} and isinstance(
        event_data,
        dict,
    ):
        notice = event_data.get("message")
        if isinstance(notice, str) and notice:
            await _send_bridge_message(f"{config.mineflayer_ws_mc_prefix}{notice}")
    elif event_type == "tpa.request_detected":
        logger.info(f"TPA request detected: {event_data}")
    else:
        logger.debug(f"收到服务端事件: {event_type} {event_data}")


async def _handle_server_command(message: dict[str, Any]) -> None:
    data = message.get("data", {})
    msg_id = message.get("msg_id")
    if not isinstance(data, dict) or not isinstance(msg_id, str):
        return

    command = data.get("command")
    args = data.get("args", [])
    if not isinstance(command, str):
        await _send_reply(msg_id, {"error_type": "invalid_message"}, status="error")
        return
    if not isinstance(args, list):
        args = []

    bot, target = resolve_nonebot_command_target(
        ws_state.active_bot,
        ws_state.active_event,
    )
    if bot is None or target is None:
        await _send_reply(
            msg_id,
            {
                "error_type": "target_unavailable",
                "error_message": "OneBot 未就绪或未绑定消息目标",
            },
            status="error",
        )
        return

    try:
        await dispatch_nonebot_command(
            bot,
            target,
            command,
            [str(arg) for arg in args],
            user_id=extract_command_user_id(data, ws_state.active_event),
            nickname=extract_command_nickname(data),
        )
    except Exception as error:
        await _send_reply(
            msg_id,
            {"error_type": "command_failed", "error_message": str(error)},
            status="error",
        )
        return

    await _send_reply(msg_id, {"command": command, "reply": "已转交 NoneBot 指令处理"})


async def _handle_server_error(message: dict[str, Any]) -> None:
    data = message.get("data", {})
    if isinstance(data, dict):
        logger.error(
            "Mineflayer WebSocket error: %s %s",
            data.get("error_type", "unknown"),
            data.get("error_message", ""),
        )
    else:
        logger.error(f"Mineflayer WebSocket error: {message}")


async def _poll_players_loop() -> None:
    while True:
        await asyncio.sleep(max(config.mineflayer_ws_player_poll_interval, 1))
        if not _is_ws_connected() or not ws_state.current_bot_id:
            continue
        try:
            reply = await _send_request("player", bot_id=ws_state.current_bot_id)
            await _record_player_reply(reply)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f"轮询在线玩家失败: {error}")


async def _record_player_reply(reply: dict[str, Any]) -> None:
    from .player_tracker import record_snapshot

    result = reply.get("data", {}).get("result", {})
    if not isinstance(result, dict):
        return

    raw_players = result.get("player", [])
    if not isinstance(raw_players, list):
        return

    players: list[dict[str, str]] = []
    for raw_player in raw_players:
        if not isinstance(raw_player, dict):
            continue
        username = raw_player.get("username")
        if not isinstance(username, str) or not username:
            continue
        players.append(
            {
                "name": username,
                "uuid": str(raw_player.get("uuid") or ""),
                "skin_url": str(raw_player.get("skin_url") or ""),
            }
        )

    bot_username = result.get("bot_username")
    record_snapshot(
        players,
        datetime.now(timezone.utc).isoformat(),
        bot_username=str(bot_username or ""),
    )


def _format_status() -> str:
    state = load_runtime_state()
    ws_text = "已连接" if _is_ws_connected() else "未连接"
    auth_text = "已认证" if ws_state.authenticated else "未认证"
    bot_text = ws_state.current_bot_id or state.get("mc_bot_id") or "未绑定"
    bot_state = ws_state.current_bot_state or state.get("mc_bot_state") or "unknown"
    target_text = format_target(state)
    pending_text = str(len(ws_state.pending_bridge_messages))
    poller_text = (
        "运行中"
        if (
            ws_state.player_poll_task is not None
            and not ws_state.player_poll_task.done()
        )
        else "未运行"
    )

    return (
        "MC WebSocket 状态：\n"
        f"- 连接: {ws_text}\n"
        f"- 认证: {auth_text}\n"
        f"- MC Bot: {bot_text}\n"
        f"- MC 状态: {bot_state}\n"
        f"- 推送目标: {target_text}\n"
        f"- 玩家轮询: {poller_text}\n"
        f"- 待补发消息: {pending_text}"
    )



async def _delegate_to_ws(
    command: str,
    args: list[str],
    level: PermissionLevel,
    *,
    player_name: str | None = None,
) -> str:
    if not _is_ws_connected():
        return "WebSocket 未连接，无法执行指令"
    if not ws_state.current_bot_id:
        return "当前未绑定 MC bot，无法执行指令"

    permission = "admin" if level >= PermissionLevel.ADMIN else "user"
    extra: dict[str, Any] = {"permission": permission}
    if player_name:
        extra["player_name"] = player_name

    try:
        reply = await _send_request(
            "command",
            {"command": command, "args": args, "wait": True},
            bot_id=ws_state.current_bot_id,
            extra=extra,
            timeout=max(config.mineflayer_ws_request_timeout, 15),
        )
    except asyncio.TimeoutError:
        return "指令执行超时"
    except Exception as error:
        return f"指令执行失败：{error}"

    result = reply.get("data", {}).get("result")
    return message_result_text(result)
