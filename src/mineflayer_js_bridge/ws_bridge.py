"""QQ 与 Minecraft 之间的消息桥接。"""

from __future__ import annotations

from nonebot import get_bots, logger
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment

from . import ws_state
from .context import config
from .utils import (
    event_matches_runtime_target,
    is_local_command_text,
    load_runtime_state,
)
from .ws_transport import _send_request

BridgeMessage = str | Message | MessageSegment


def _normalize_bridge_message(message: BridgeMessage) -> str | Message:
    if isinstance(message, MessageSegment):
        return Message(message)
    return message


def _is_empty_bridge_message(message: BridgeMessage) -> bool:
    if isinstance(message, str):
        return not message
    if isinstance(message, Message):
        return len(message) == 0
    return False


async def _send_bridge_message(message: BridgeMessage) -> None:
    if _is_empty_bridge_message(message):
        return

    delivered = await _try_send_bridge_message(message)
    if delivered:
        return

    ws_state.pending_bridge_messages.append(message)
    logger.warning("桥接消息暂存：OneBot 尚未就绪或目标不可用，等待连接恢复后补发")


async def _try_send_bridge_message(message: BridgeMessage) -> bool:
    if _is_empty_bridge_message(message):
        return True
    normalized_message = _normalize_bridge_message(message)

    if ws_state.active_bot and ws_state.active_event:
        try:
            await ws_state.active_bot.send(ws_state.active_event, normalized_message)
            return True
        except Exception as error:
            logger.error(f"通过事件上下文发送消息失败: {error}")

    state = load_runtime_state()
    onebot_id = state.get("onebot_id")
    target_type = state.get("target_type")
    target_id = state.get("target_id")

    if not isinstance(onebot_id, str) or target_type not in {"group", "private"}:
        return False
    if not isinstance(target_id, int):
        return False

    bot_instance = get_bots().get(onebot_id)
    if bot_instance is None:
        return False

    try:
        if target_type == "group":
            await bot_instance.call_api(
                "send_group_msg",
                group_id=target_id,
                message=normalized_message,
            )
        else:
            await bot_instance.call_api(
                "send_private_msg",
                user_id=target_id,
                message=normalized_message,
            )
        return True
    except Exception as error:
        logger.error(f"发送桥接消息失败: {error}")
        return False


async def _flush_pending_bridge_messages() -> None:
    if not ws_state.pending_bridge_messages:
        return

    pending = list(ws_state.pending_bridge_messages)
    ws_state.pending_bridge_messages.clear()
    sent_count = 0

    for index, pending_message in enumerate(pending):
        delivered = await _try_send_bridge_message(pending_message)
        if delivered:
            sent_count += 1
            continue

        # 首条失败后保留剩余消息顺序，等待下一次目标恢复再补发。
        for remaining_message in pending[index:]:
            ws_state.pending_bridge_messages.append(remaining_message)
        break

    if sent_count > 0:
        logger.info(f"已补发桥接消息 {sent_count} 条")


async def forward_onebot_message(bot: Bot, event: Event) -> None:
    if ws_state.ws_connection is None or not ws_state.current_bot_id:
        return
    if not event_matches_runtime_target(
        bot,
        event,
        ws_state.active_bot,
        ws_state.active_event,
    ):
        return

    sender_id = getattr(event, "user_id", None)
    if sender_id is not None and str(sender_id) == str(bot.self_id):
        return

    plain_text = (
        event.get_plaintext().strip() if hasattr(event, "get_plaintext") else ""
    )
    if not plain_text:
        return
    if is_local_command_text(plain_text):
        return
    if plain_text.startswith(config.mineflayer_ws_mc_prefix):
        return

    try:
        await _send_request(
            "message",
            {
                "type": "chat",
                "prefix": config.mineflayer_ws_forward_prefix,
                "target_player": None,
                "content": plain_text,
            },
            bot_id=ws_state.current_bot_id,
            timeout=config.mineflayer_ws_request_timeout,
        )
    except Exception as error:
        logger.warning(f"转发 QQ 消息到 MC 失败: {error}")
