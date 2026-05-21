from __future__ import annotations

from pathlib import Path

import nonebot
from nonebot import on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.typing import T_State

from .config import Config
from .context import config
from .utils import parse_positive_int
from .ws import (
    _close_ws_connection,
    _connect_ws,
    _delegate_to_ws,
    _execute_git_pull,
    _format_status,
    _is_ws_connected,
    _logout_current_bot,
    dispatch_command,
    forward_onebot_message,
)

__all__ = [
    "_close_ws_connection",
    "_execute_git_pull",
    "_is_ws_connected",
    "config",
    "dispatch_command",
]

try:
    from src.utils.command_reaction import (
        enable_status_reaction_hooks,
        mark_status_reaction_success,
    )
    from src.utils.permission import ADMIN, PermissionLevel
    from src.utils.trigger import to_me_or_prefix
except ModuleNotFoundError:
    from utils.command_reaction import (
        enable_status_reaction_hooks,
        mark_status_reaction_success,
    )
    from utils.permission import ADMIN, PermissionLevel
    from utils.trigger import to_me_or_prefix

__plugin_meta__ = PluginMetadata(
    name="mc",
    description="MC WebSocket 连接管理",
    usage="mc <connect|disconnect|logout|status>",
    config=Config,
    extra={"group": "MC"},
)

sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent.joinpath("plugins").resolve())
)

mc = on_command(
    "mc",
    rule=to_me_or_prefix(),
    aliases={"minecraft"},
    priority=5,
    permission=ADMIN,
)
tpa_cmd = on_command("tpa", rule=to_me_or_prefix(), priority=5, permission=ADMIN)
home_cmd = on_command("home", rule=to_me_or_prefix(), priority=5, permission=ADMIN)
bridge_input = on_message(priority=20, block=False)

enable_status_reaction_hooks(mc, tpa_cmd, home_cmd)


@mc.handle()
async def _(
    bot: Bot,
    event: Event,
    state: T_State,
    args: Message = CommandArg(),
) -> None:
    arg_list = args.extract_plain_text().strip().split()
    command = arg_list[0] if arg_list else ""

    if command == "connect":
        account = parse_positive_int(arg_list[1]) if len(arg_list) > 1 else None
        server = parse_positive_int(arg_list[2]) if len(arg_list) > 2 else None
        success, message = await _connect_ws(
            bot=bot,
            event=event,
            account_preset=account,
            server_preset=server,
            persist_state=True,
        )
        await mc.send(message)
        mark_status_reaction_success(state, success or _is_ws_connected())
        return

    if command == "disconnect":
        stopped, message = await _close_ws_connection(persist_state=True)
        await mc.send(message)
        mark_status_reaction_success(state, stopped)
        return

    if command == "logout":
        success, message = await _logout_current_bot()
        await mc.send(message)
        mark_status_reaction_success(state, success)
        return

    if command == "status":
        await mc.send(_format_status())
        mark_status_reaction_success(state, True)
        return

    await mc.send("用法: mc <connect|disconnect|logout|status>")
    mark_status_reaction_success(state, False)


@tpa_cmd.handle()
async def _(state: T_State, args: Message = CommandArg()) -> None:
    arg_text = args.extract_plain_text().strip()
    arg_list = arg_text.split() if arg_text else []
    result = await _delegate_to_ws("tpa", arg_list, PermissionLevel.ADMIN)
    if result:
        await tpa_cmd.send(result)

    success = "失败" not in result and "不足" not in result and "超时" not in result
    mark_status_reaction_success(state, success)


@home_cmd.handle()
async def _(state: T_State, args: Message = CommandArg()) -> None:
    arg_text = args.extract_plain_text().strip()
    arg_list = arg_text.split() if arg_text else []
    result = await _delegate_to_ws("home", arg_list, PermissionLevel.ADMIN)
    if result:
        await home_cmd.send(result)

    success = "失败" not in result and "超时" not in result
    mark_status_reaction_success(state, success)


@bridge_input.handle()
async def _(bot: Bot, event: Event) -> None:
    await forward_onebot_message(bot, event)
