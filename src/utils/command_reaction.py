"""OneBot 指令消息状态表情工具。"""

from __future__ import annotations

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot

EMOJI_STATUS_PROCESSING = "424"
EMOJI_STATUS_SUCCESS = "287"
EMOJI_STATUS_FAILED = "128560"

_STATUS_EMOJIS = [
    EMOJI_STATUS_PROCESSING,
    EMOJI_STATUS_SUCCESS,
    EMOJI_STATUS_FAILED,
]


async def set_status_emoji(bot: Bot, message_id: int | None, target_emoji_id: str) -> None:
    """为触发消息切换状态表情：先移除旧状态，再添加新状态。"""
    if message_id is None:
        return

    for emoji_id in _STATUS_EMOJIS:
        if emoji_id == target_emoji_id:
            continue
        await _call_set_msg_emoji_like(
            bot=bot,
            message_id=message_id,
            emoji_id=emoji_id,
            is_add=False,
        )

    await _call_set_msg_emoji_like(
        bot=bot,
        message_id=message_id,
        emoji_id=target_emoji_id,
        is_add=True,
    )


async def _call_set_msg_emoji_like(
    bot: Bot,
    message_id: int,
    emoji_id: str,
    is_add: bool,
) -> bool:
    """调用 set_msg_emoji_like，兼容不同实现的参数差异。"""
    attempts: list[dict[str, object]] = []

    if is_add:
        attempts.append({"message_id": message_id, "emoji_id": emoji_id})

    attempts.extend(
        [
            {"message_id": message_id, "emoji_id": emoji_id, "set": is_add},
            {"message_id": message_id, "emoji_id": emoji_id, "is_add": is_add},
            {"message_id": message_id, "emoji_id": emoji_id, "delete": not is_add},
        ],
    )

    for payload in attempts:
        try:
            await bot.call_api("set_msg_emoji_like", **payload)
            return True
        except Exception as error:
            logger.debug(
                "set_msg_emoji_like 调用失败, payload=%s, error=%s",
                payload,
                error,
            )

    return False
