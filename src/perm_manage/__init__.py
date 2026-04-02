"""权限管理指令插件。

指令格式: perm <add|rm|list|check> [QQ号/@某人]

示例:
  @bot perm add 123456789     → 授予 admin 权限
  @bot perm add @某人         → 授予 admin 权限（通过 @）
  @bot perm rm 123456789      → 移除 admin 权限
  @bot perm list              → 查看所有 admin
  @bot perm check             → 查看自己的权限
  @bot perm check @某人       → 查看指定用户的权限
"""

from __future__ import annotations

import re

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me

try:
    from src.utils.command_reaction import (
        EMOJI_STATUS_FAILED,
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )
    from src.utils.permission import (
        PermissionLevel,
        add_admin,
        get_permission_level,
        list_admins,
        remove_admin,
    )
except ModuleNotFoundError:
    from utils.command_reaction import (
        EMOJI_STATUS_FAILED,
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )
    from utils.permission import (
        PermissionLevel,
        add_admin,
        get_permission_level,
        list_admins,
        remove_admin,
    )

__plugin_meta__ = PluginMetadata(
    name="perm-manage",
    description="三级权限管理（user/admin/super）",
    usage="perm <add|rm|list|check> [QQ号/@某人]",
)

# ===== 指令注册 =====

perm_cmd = on_command("perm", rule=to_me(), aliases={"权限"}, priority=5)


# ===== 工具函数 =====

_AT_PATTERN = re.compile(r"\[CQ:at,qq=(\d+)]")


def _extract_target_id(args: Message) -> str | None:
    """从消息参数中提取目标用户 ID（支持 @某人 和直接输入 QQ 号）。"""
    for seg in args:
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq:
                return str(qq)

    # 回退：从纯文本中提取数字
    plain = args.extract_plain_text().strip()
    parts = plain.split()
    for part in parts:
        if part.isdigit() and len(part) >= 5:  # noqa: PLR2004 — QQ 号至少 5 位
            return part

    return None


def _level_display(level: PermissionLevel) -> str:
    """权限等级的中文显示。"""
    labels = {
        PermissionLevel.USER: "👤 user（普通用户）",
        PermissionLevel.ADMIN: "🛡️ admin（管理员）",
        PermissionLevel.SUPER: "👑 super（超级管理员）",
    }
    return labels.get(level, str(level))


# ===== 指令处理 =====


@perm_cmd.handle()
async def handle_perm(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    """处理 perm 指令。"""
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    plain = args.extract_plain_text().strip()
    parts = plain.split()
    sub = parts[0] if parts else ""

    if sub == "add":
        await _handle_add(bot, event, args, message_id)

    elif sub in {"rm", "remove", "del"}:
        await _handle_rm(bot, event, args, message_id)

    elif sub == "list":
        await _handle_list(bot, event, message_id)

    elif sub == "check":
        await _handle_check(bot, event, args, message_id)

    else:
        await perm_cmd.send(
            "用法：perm <add|rm|list|check> [QQ号/@某人]\n"
            "  add   - 授予 admin 权限（需 super）\n"
            "  rm    - 移除 admin 权限（需 super）\n"
            "  list  - 查看所有 admin\n"
            "  check - 查看权限等级",
        )
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)


async def _handle_add(
    bot: Bot,
    event: MessageEvent,
    args: Message,
    message_id: int | None,
) -> None:
    """处理 perm add。"""
    # 权限检查：仅 super
    sender_id = str(getattr(event, "user_id", ""))
    sender_level = get_permission_level(sender_id)
    if sender_level != PermissionLevel.SUPER:
        await perm_cmd.send("权限不足：仅 super 可添加 admin")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        return

    # 提取目标用户（跳过第一个参数 "add"）
    # 从 @ 消息段或文本中提取
    remaining_args = _strip_first_text_part(args)
    target_id = _extract_target_id(remaining_args)

    if not target_id:
        await perm_cmd.send("请指定用户：perm add <QQ号/@某人>")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        return

    target_level = get_permission_level(target_id)
    if target_level == PermissionLevel.SUPER:
        await perm_cmd.send(f"{target_id} 已是 super，无需添加为 admin")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        return

    if add_admin(target_id):
        await perm_cmd.send(f"✅ 已授予 {target_id} admin 权限")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)
    else:
        await perm_cmd.send(f"{target_id} 已是 admin")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)


async def _handle_rm(
    bot: Bot,
    event: MessageEvent,
    args: Message,
    message_id: int | None,
) -> None:
    """处理 perm rm。"""
    sender_id = str(getattr(event, "user_id", ""))
    sender_level = get_permission_level(sender_id)
    if sender_level != PermissionLevel.SUPER:
        await perm_cmd.send("权限不足：仅 super 可移除 admin")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        return

    remaining_args = _strip_first_text_part(args)
    target_id = _extract_target_id(remaining_args)

    if not target_id:
        await perm_cmd.send("请指定用户：perm rm <QQ号/@某人>")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        return

    if remove_admin(target_id):
        await perm_cmd.send(f"✅ 已移除 {target_id} 的 admin 权限")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)
    else:
        await perm_cmd.send(f"{target_id} 不是 admin")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)


async def _handle_list(
    bot: Bot,
    event: MessageEvent,
    message_id: int | None,
) -> None:
    """处理 perm list。"""
    # admin+ 可用
    sender_id = str(getattr(event, "user_id", ""))
    sender_level = get_permission_level(sender_id)
    if sender_level < PermissionLevel.ADMIN:
        await perm_cmd.send("权限不足：admin 及以上可查看")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        return

    admins = list_admins()
    if not admins:
        await perm_cmd.send("当前没有 admin")
    else:
        lines = [f"当前 admin 列表（共 {len(admins)} 人）："]
        for uid in admins:
            lines.append(f"  • {uid}")
        await perm_cmd.send("\n".join(lines))

    await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)


async def _handle_check(
    bot: Bot,
    event: MessageEvent,
    args: Message,
    message_id: int | None,
) -> None:
    """处理 perm check。"""
    remaining_args = _strip_first_text_part(args)
    target_id = _extract_target_id(remaining_args)

    if not target_id:
        # 查看自己
        target_id = str(getattr(event, "user_id", ""))

    level = get_permission_level(target_id)
    await perm_cmd.send(f"{target_id} 的权限等级：{_level_display(level)}")
    await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)


def _strip_first_text_part(msg: Message) -> Message:
    """移除消息中第一个 text 段的第一个词（子指令名），保留其余内容。"""
    result = msg.copy()
    for i, seg in enumerate(result):
        if seg.type == "text":
            text = str(seg.data.get("text", "")).strip()
            parts = text.split(maxsplit=1)
            if len(parts) > 1:
                result[i] = MessageSegment.text(parts[1])
            else:
                result[i] = MessageSegment.text("")
            break
    return result
