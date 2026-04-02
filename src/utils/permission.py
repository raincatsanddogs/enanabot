"""三级权限管理模块（user / admin / super）。

权限层级:
  - super: .env SUPERUSERS，不可变
  - admin: 由 super 授权，持久化存储于 configs/admins.json
  - user:  默认权限
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.permission import SUPERUSER, Permission


# ===== 权限等级枚举 =====


class PermissionLevel(str, Enum):
    """权限等级，值同时用于 IPC / dispatch 场景的字符串标识。"""

    USER = "user"
    ADMIN = "admin"
    SUPER = "super"

    def __ge__(self, other: "PermissionLevel") -> bool:  # noqa: PYI034
        order = {PermissionLevel.USER: 0, PermissionLevel.ADMIN: 1, PermissionLevel.SUPER: 2}
        return order[self] >= order[other]

    def __gt__(self, other: "PermissionLevel") -> bool:  # noqa: PYI034
        order = {PermissionLevel.USER: 0, PermissionLevel.ADMIN: 1, PermissionLevel.SUPER: 2}
        return order[self] > order[other]

    def __le__(self, other: "PermissionLevel") -> bool:  # noqa: PYI034
        order = {PermissionLevel.USER: 0, PermissionLevel.ADMIN: 1, PermissionLevel.SUPER: 2}
        return order[self] <= order[other]

    def __lt__(self, other: "PermissionLevel") -> bool:  # noqa: PYI034
        order = {PermissionLevel.USER: 0, PermissionLevel.ADMIN: 1, PermissionLevel.SUPER: 2}
        return order[self] < order[other]


# ===== Admin 持久化 =====

_ADMINS_PATH = Path(__file__).resolve().parents[2] / "configs" / "admins.json"

# 内存缓存，避免每次查询都读磁盘
_admins_cache: set[str] | None = None


def _load_admins() -> set[str]:
    """从 JSON 文件加载 admin 列表到内存。"""
    global _admins_cache  # noqa: PLW0603

    if not _ADMINS_PATH.exists():
        _admins_cache = set()
        return _admins_cache

    try:
        data = json.loads(_ADMINS_PATH.read_text(encoding="utf-8"))
        admins = data.get("admins", [])
        _admins_cache = {str(uid) for uid in admins if uid}
    except Exception as error:
        logger.warning(f"读取 admins.json 失败，使用空列表: {error}")
        _admins_cache = set()

    return _admins_cache


def _save_admins() -> None:
    """将当前内存中的 admin 列表持久化到 JSON 文件。"""
    from datetime import datetime, timezone

    if _admins_cache is None:
        return

    payload = {
        "admins": sorted(_admins_cache),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    try:
        _ADMINS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ADMINS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as error:
        logger.error(f"写入 admins.json 失败: {error}")


def _get_admins() -> set[str]:
    """获取 admin 集合（优先使用缓存）。"""
    if _admins_cache is None:
        return _load_admins()
    return _admins_cache


# ===== 公开 API =====


def get_superusers() -> set[str]:
    """获取 .env 中配置的 SUPERUSERS 集合。"""
    return {str(uid) for uid in get_driver().config.superusers}


def get_permission_level(user_id: str) -> PermissionLevel:
    """根据用户 ID 判断权限等级。"""
    uid = str(user_id)

    if uid in get_superusers():
        return PermissionLevel.SUPER

    if uid in _get_admins():
        return PermissionLevel.ADMIN

    return PermissionLevel.USER


def add_admin(user_id: str) -> bool:
    """添加 admin。返回 True 表示新增成功，False 表示已存在。"""
    uid = str(user_id)

    if uid in get_superusers():
        return False  # super 不需要额外添加为 admin

    admins = _get_admins()
    if uid in admins:
        return False

    admins.add(uid)
    _save_admins()
    logger.info(f"已添加 admin: {uid}")
    return True


def remove_admin(user_id: str) -> bool:
    """移除 admin。返回 True 表示移除成功，False 表示不存在。"""
    uid = str(user_id)
    admins = _get_admins()

    if uid not in admins:
        return False

    admins.discard(uid)
    _save_admins()
    logger.info(f"已移除 admin: {uid}")
    return True


def list_admins() -> list[str]:
    """列出所有 admin ID（已排序）。"""
    return sorted(_get_admins())


# ===== NoneBot Permission 对象 =====


async def _check_admin(bot: Bot, event: Event) -> bool:
    """检查事件发送者是否为 admin 或 super。"""
    user_id = getattr(event, "user_id", None)
    if user_id is None:
        return False
    level = get_permission_level(str(user_id))
    return level >= PermissionLevel.ADMIN


ADMIN = Permission(_check_admin)
"""NoneBot Permission：admin 和 super 均可通过。"""
