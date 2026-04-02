"""帮助指令插件。

指令: help
  @bot help  或  #help  → 显示所有可用命令（分组）
"""

from __future__ import annotations

from nonebot import get_loaded_plugins, on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.plugin import PluginMetadata

try:
    from src.utils.command_reaction import (
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )
    from src.utils.trigger import to_me_or_prefix
except ModuleNotFoundError:
    from utils.command_reaction import (
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )
    from utils.trigger import to_me_or_prefix

__plugin_meta__ = PluginMetadata(
    name="help",
    description="显示所有可用命令",
    usage="help",
    extra={"group": "通用", "order": 0},
)

# ===== 插件分组配置 =====
# 通过 PluginMetadata.extra["group"] 指定分组
# 未指定分组的插件归入 "其他"
# 隐藏不需要显示的内部插件
_HIDDEN_PLUGINS: set[str] = {
    "echo",
    "nonebot_plugin_echo",
    "utils",
}

# 分组显示顺序
_GROUP_ORDER: list[str] = [
    "通用",
    "MC",
    "管理",
    "其他",
]

help_cmd = on_command("help", rule=to_me_or_prefix(), aliases={"帮助"}, priority=5)


@help_cmd.handle()
async def handle_help(bot: Bot, event: MessageEvent) -> None:
    """处理 help 指令，分组显示所有命令。"""
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    groups: dict[str, list[tuple[str, str, str]]] = {}

    for plugin in get_loaded_plugins():
        meta = plugin.metadata
        if meta is None:
            continue

        name = meta.name or plugin.name
        if name in _HIDDEN_PLUGINS or plugin.name in _HIDDEN_PLUGINS:
            continue

        description = meta.description or ""
        usage = meta.usage or ""
        group = (meta.extra or {}).get("group", "其他")

        groups.setdefault(group, []).append((name, description, usage))

    # 按配置顺序排列分组
    sorted_groups: list[tuple[str, list[tuple[str, str, str]]]] = []
    for group_name in _GROUP_ORDER:
        if group_name in groups:
            items = sorted(
                groups.pop(group_name),
                key=lambda x: (x[0] if not isinstance((x[0]), str) else x[0]),
            )
            sorted_groups.append((group_name, items))

    # 未在排序列表中的额外分组
    for group_name in sorted(groups.keys()):
        items = sorted(groups[group_name], key=lambda x: x[0])
        sorted_groups.append((group_name, items))

    # 构建输出
    lines: list[str] = ["📋 可用命令列表", ""]

    for group_name, items in sorted_groups:
        lines.append(f"【{group_name}】")
        for name, description, usage in items:
            line = f"  • {name}"
            if description:
                line += f" — {description}"
            lines.append(line)
            if usage:
                lines.append(f"    用法: {usage}")
        lines.append("")

    lines.append("💡 触发方式: @bot 命令 或 #命令")

    await help_cmd.send("\n".join(lines))
    await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)
