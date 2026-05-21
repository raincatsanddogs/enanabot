"""OneBot 指令消息状态表情工具。"""

from __future__ import annotations

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor, run_preprocessor

EMOJI_STATUS_PROCESSING = "424"
EMOJI_STATUS_SUCCESS = "127847"
EMOJI_STATUS_FAILED = "128560"
STATUS_REACTION_RESULT_KEY = "_status_reaction_success"

_STATUS_EMOJIS = [
    EMOJI_STATUS_PROCESSING,
    EMOJI_STATUS_SUCCESS,
    EMOJI_STATUS_FAILED,
]
_HOOKED_MATCHERS: set[type[Matcher]] = set()
_HOOKS_INSTALLED = False


def enable_status_reaction_hooks(*matcher_types: type[Matcher]) -> None:
    """为指定 matcher 启用指令状态表情 hook。"""
    global _HOOKS_INSTALLED

    _HOOKED_MATCHERS.update(matcher_types)
    if _HOOKS_INSTALLED:
        return

    _HOOKS_INSTALLED = True

    @run_preprocessor
    async def _set_processing_status(bot: Bot, event, matcher: Matcher) -> None:
        if matcher.__class__ not in _HOOKED_MATCHERS:
            return

        message_id = _extract_message_id(event)
        await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    @run_postprocessor
    async def _set_finished_status(
        bot: Bot,
        event,
        matcher: Matcher,
        exception,
    ) -> None:
        if matcher.__class__ not in _HOOKED_MATCHERS:
            return

        message_id = _extract_message_id(event)
        success = exception is None and matcher.state.get(
            STATUS_REACTION_RESULT_KEY,
            True,
        )
        await set_status_emoji(
            bot,
            message_id,
            EMOJI_STATUS_SUCCESS if success else EMOJI_STATUS_FAILED,
        )


def mark_status_reaction_success(state: dict, success: bool) -> None:
    """在 handler 中标记状态表情最终应显示成功或失败。"""
    state[STATUS_REACTION_RESULT_KEY] = success


async def set_status_emoji(
    bot: Bot,
    message_id: int | None,
    target_emoji_id: str,
) -> None:
    """为触发消息切换状态表情：先移除旧状态，再添加新状态。"""
    if message_id is None or message_id < 0:
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


def _extract_message_id(event) -> int | None:
    message_id = getattr(event, "message_id", None)
    return message_id if isinstance(message_id, int) else None
