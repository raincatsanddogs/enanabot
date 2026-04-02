"""可配置前缀触发 Rule。

支持两种触发方式：
1. @bot（to_me）
2. 消息以配置的前缀开头（如 #、! 等）

在 .env 中通过 BOT_COMMAND_PREFIX 配置，支持多个前缀：
  BOT_COMMAND_PREFIX=["#", "!"]
"""

from __future__ import annotations

from nonebot import get_driver
from nonebot.adapters import Event
from nonebot.rule import Rule, to_me


def _get_command_prefixes() -> list[str]:
    """从 .env 配置中读取 BOT_COMMAND_PREFIX。

    支持字符串或列表格式：
      BOT_COMMAND_PREFIX="#"
      BOT_COMMAND_PREFIX=["#", "!"]
    """
    raw = getattr(get_driver().config, "bot_command_prefix", None)
    if raw is None:
        return ["#"]

    if isinstance(raw, str):
        return [raw] if raw else []

    if isinstance(raw, (list, tuple, set)):
        return [str(p) for p in raw if p]

    return [str(raw)]


def to_me_or_prefix() -> Rule:
    """自定义 Rule：@bot 或前缀触发均可。

    - 若消息已 to_me，直接放行。
    - 否则检查消息纯文本是否以任一配置前缀开头。
    """

    async def _checker(event: Event) -> bool:
        # 1. @bot 触发
        if event.is_tome():
            return True

        # 2. 前缀触发
        prefixes = _get_command_prefixes()
        if not prefixes:
            return False

        plain_text = event.get_plaintext().strip() if hasattr(event, "get_plaintext") else ""
        if not plain_text:
            return False

        return any(plain_text.startswith(p) for p in prefixes)

    return Rule(_checker)

