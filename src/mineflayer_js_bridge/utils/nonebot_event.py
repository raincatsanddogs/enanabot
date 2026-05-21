from __future__ import annotations

import time
from typing import Any

from nonebot import get_bots, get_driver, logger
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.message import handle_event

try:
    from src.utils.permission import list_admins
except ModuleNotFoundError:
    from utils.permission import list_admins

from .runtime_state import (
    extract_target_from_event,
    load_runtime_state,
    runtime_event_matches_target,
)


async def dispatch_nonebot_command(
    bot: Bot,
    target: dict[str, Any],
    command: str,
    args: list[str],
    *,
    user_id: int | None = None,
    nickname: str | None = None,
) -> None:
    """Build a OneBot message event and let NoneBot route it normally."""
    message_text = _build_command_message(command, args)
    message = Message(message_text)
    sender_id = _resolve_sender_id(user_id, target)
    event_payload = {
        "time": int(time.time()),
        "self_id": _to_int(bot.self_id),
        "post_type": "message",
        "sub_type": "normal",
        "user_id": sender_id,
        "message_id": -int(time.time() * 1000),
        "message": message,
        "raw_message": message_text,
        "font": 0,
        "sender": Sender(
            user_id=sender_id,
            nickname=nickname or "Mineflayer",
            role="admin",
        ),
        "to_me": True,
        "_mineflayer_synthetic": True,
    }

    target_type = target.get("target_type")
    target_id = target.get("target_id")
    if target_type == "group" and isinstance(target_id, int):
        event = GroupMessageEvent(
            **event_payload,
            message_type="group",
            group_id=target_id,
        )
    elif target_type == "private" and isinstance(target_id, int):
        event = PrivateMessageEvent(
            **event_payload,
            message_type="private",
        )
    else:
        msg = f"无效的 NoneBot 指令目标: {target}"
        raise RuntimeError(msg)

    logger.info(f"JS 指令已转交 NoneBot 处理: {message_text}")
    await handle_event(bot, event)


def _build_command_message(command: str, args: list[str]) -> str:
    text = command if not args else f"{command} {' '.join(args)}"
    return f"/{text}"


def _resolve_sender_id(user_id: int | None, target: dict[str, Any]) -> int:
    if isinstance(user_id, int) and user_id > 0:
        return user_id

    target_type = target.get("target_type")
    target_id = target.get("target_id")
    if target_type == "private" and isinstance(target_id, int):
        return target_id

    superusers = sorted(str(uid) for uid in get_driver().config.superusers)
    for uid in superusers:
        if uid.isdigit():
            return int(uid)

    for uid in list_admins():
        if uid.isdigit():
            return int(uid)

    return 0


def _to_int(value: object) -> int:
    text = str(value)
    return int(text) if text.isdigit() else 0


def event_matches_runtime_target(
    bot: Bot,
    event: Event,
    active_bot: Bot | None,
    active_event: Event | None,
) -> bool:
    """检查 OneBot 事件是否属于当前绑定的桥接目标。"""
    if active_bot and active_event:
        if str(bot.self_id) != str(active_bot.self_id):
            return False
        active_target = extract_target_from_event(active_bot, active_event)
        if active_target:
            return runtime_event_matches_target(event, active_target)

    state = load_runtime_state()
    onebot_id = state.get("onebot_id")
    if isinstance(onebot_id, str) and onebot_id and str(bot.self_id) != onebot_id:
        return False
    return runtime_event_matches_target(event, state)


def resolve_nonebot_command_target(
    active_bot: Bot | None,
    active_event: Event | None,
) -> tuple[Bot | None, dict[str, Any] | None]:
    """解析服务端指令应投递到的 OneBot bot 和目标会话。"""
    if active_bot and active_event:
        active_target = extract_target_from_event(active_bot, active_event)
        if active_target:
            return active_bot, active_target

    state = load_runtime_state()
    onebot_id = state.get("onebot_id")
    target_type = state.get("target_type")
    target_id = state.get("target_id")
    if not isinstance(onebot_id, str) or target_type not in {"group", "private"}:
        return None, None
    if not isinstance(target_id, int):
        return None, None

    return get_bots().get(onebot_id), {
        "onebot_id": onebot_id,
        "target_type": target_type,
        "target_id": target_id,
    }


def extract_command_user_id(
    data: dict[str, Any],
    active_event: Event | None = None,
) -> int | None:
    raw_user_id = data.get("user_id") or data.get("qq") or data.get("sender_id")
    if isinstance(raw_user_id, int):
        return raw_user_id
    if isinstance(raw_user_id, str) and raw_user_id.isdigit():
        return int(raw_user_id)
    if active_event is not None:
        event_user_id = getattr(active_event, "user_id", None)
        if isinstance(event_user_id, int):
            return event_user_id
    return None


def extract_command_nickname(data: dict[str, Any]) -> str | None:
    raw_name = data.get("nickname") or data.get("player_name") or data.get("username")
    return raw_name if isinstance(raw_name, str) and raw_name else None


def is_local_command_text(text: str) -> bool:
    command_prefixes = ("/", "#")
    local_commands = ("mc", "connect", "tpa", "home", "git")
    return any(
        text.startswith(f"{prefix}{command}")
        for prefix in command_prefixes
        for command in local_commands
    )
