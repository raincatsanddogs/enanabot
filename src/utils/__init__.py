"""通用工具模块。"""

from .command_reaction import (
    EMOJI_STATUS_FAILED,
    EMOJI_STATUS_PROCESSING,
    EMOJI_STATUS_SUCCESS,
    set_status_emoji,
)
from .permission import (
    ADMIN,
    PermissionLevel,
    add_admin,
    get_permission_level,
    get_superusers,
    list_admins,
    remove_admin,
)
from .trigger import to_me_or_prefix

__all__ = [
    "ADMIN",
    "EMOJI_STATUS_FAILED",
    "EMOJI_STATUS_PROCESSING",
    "EMOJI_STATUS_SUCCESS",
    "PermissionLevel",
    "add_admin",
    "get_permission_level",
    "get_superusers",
    "list_admins",
    "remove_admin",
    "set_status_emoji",
    "to_me_or_prefix",
]
