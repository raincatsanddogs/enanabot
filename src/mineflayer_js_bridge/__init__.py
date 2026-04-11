import asyncio
import json
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import nonebot
from nonebot import get_bots, get_driver, get_plugin_config, logger, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .config import Config

try:
    from src.utils.command_reaction import (
        EMOJI_STATUS_FAILED,
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )
    from src.utils.permission import ADMIN, PermissionLevel
    from src.utils.trigger import to_me_or_prefix
except ModuleNotFoundError:
    from utils.command_reaction import (
        EMOJI_STATUS_FAILED,
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )
    from utils.permission import ADMIN, PermissionLevel
    from utils.trigger import to_me_or_prefix

__plugin_meta__ = PluginMetadata(
    name="mc",
    description="MC 服务器连接管理",
    usage="mc <start|stop|status>",
    config=Config,
    extra={"group": "MC"},
)

config = get_plugin_config(Config)

sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent.joinpath("plugins").resolve())
)

mc = on_command("mc", rule=to_me_or_prefix(), aliases={"minecraft"}, priority=5, permission=ADMIN)
tpa_cmd = on_command("tpa", rule=to_me_or_prefix(), priority=5, permission=ADMIN)
home_cmd = on_command("home", rule=to_me_or_prefix(), priority=5, permission=ADMIN)
bridge_input = on_message(priority=20, block=False)
JS_START_DELAY_SECONDS = 1

active_bot: Bot | None = None      # 保存当前的 Bot 实例
active_event: Event | None = None  # 保存触发指令的事件上下文

js_process: asyncio.subprocess.Process | None = None
js_stop_requested = False
js_stderr_lines: list[str] = []
PENDING_BRIDGE_MESSAGES: deque[str] = deque(maxlen=50)

RUNTIME_STATE_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "mineflayer_js_bridge.runtime.json"
)
RUNTIME_STATE_DEFAULT: dict[str, bool | str | int | None] = {
    "should_start": False,
    "bot_id": None,
    "target_type": None,
    "target_id": None,
}

# ===== TPA 状态管理 =====
TPA_STATE_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "tpa_state.json"
)
TPA_STATE_DEFAULT: dict[str, bool | str | None] = {
    "enabled": False,
    "occupied": False,
    "occupied_by": None,
    "has_backup_home": False,
}
TPA_STATE: dict[str, bool | str | None] = TPA_STATE_DEFAULT.copy()

# Home 命令结果等待（用于异步 IPC 响应）
HOME_RESULT_PENDING: dict[str, asyncio.Future] = {}


def _load_tpa_state() -> dict[str, bool | str | None]:
    """从 JSON 文件加载 TPA 状态，文件不存在时返回默认值。"""
    if not TPA_STATE_PATH.exists():
        return TPA_STATE_DEFAULT.copy()
    try:
        with TPA_STATE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {**TPA_STATE_DEFAULT, **data}
    except Exception as e:
        logger.warning(f"Failed to load TPA state: {e}")
    return TPA_STATE_DEFAULT.copy()


def _save_tpa_state() -> None:
    """保存当前 TPA 状态到 JSON 文件。"""
    try:
        TPA_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TPA_STATE_PATH.open("w", encoding="utf-8") as f:
            json.dump(TPA_STATE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save TPA state: {e}")

# ===== 统一 IPC 协议常量 =====
# Py → JS
IPC_ACTION_QQ_MESSAGE = "qq_message"
IPC_ACTION_WHISPER_REPLY = "whisper_reply"
IPC_ACTION_TPA_UPDATE_STATE = "tpa_update_state"
IPC_ACTION_HOME_COMMAND = "home_command"
# JS → Py
IPC_ACTION_MC_MESSAGE = "mc_message"
IPC_ACTION_WHISPER_COMMAND = "whisper_command"
IPC_ACTION_PLAYER_LIST = "player_list"
IPC_ACTION_TPA_OCCUPIED = "tpa_occupied"
IPC_ACTION_TPA_REQUEST_DETECTED = "tpa_request_detected"
IPC_ACTION_HOME_RESULT = "home_result"
IPC_ACTION_REQUEST_TPA_STATE = "request_tpa_state"
IPC_ACTION_TPA_NOTIFICATION = "tpa_notification"


def _ipc_encode(action: str, data: dict[str, object]) -> str:
    """编码一条统一 IPC 消息为 JSON 行。"""
    envelope = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    return json.dumps(envelope, ensure_ascii=False) + "\n"


def _ipc_decode(line: str) -> dict[str, object] | None:
    """解码一行 JSON 为 IPC envelope，返回 None 表示不是有效 IPC 消息。"""
    trimmed = line.strip()
    if not trimmed:
        return None

    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None
    if "action" not in parsed:
        return None

    return {
        "action": parsed["action"],
        "timestamp": parsed.get("timestamp", ""),
        "data": parsed.get("data", {}),
    }


# ===== 指令权限定义 =====
# admin+: 可执行所有指令
# user: 仅可执行此白名单中的指令
USER_ALLOWED_COMMANDS: set[str] = {"mc status", "tpa status", "tpa back", "home list"}


# ===== 统一指令调度器 =====
async def dispatch_command(
    command: str,
    args: list[str],
    permission_level: str | PermissionLevel,
    player_name: str | None = None,
) -> str:
    """
    统一指令入口。QQ 群指令和 MC whisper 指令共用此函数。

    返回值为指令执行结果的文本。
    """
    # 标准化为 PermissionLevel 枚举
    if isinstance(permission_level, str):
        try:
            level = PermissionLevel(permission_level)
        except ValueError:
            level = PermissionLevel.USER
    else:
        level = permission_level

    full_command = command
    if args:
        full_command = f"{command} {' '.join(args)}"

    # 权限检查
    if level < PermissionLevel.ADMIN and full_command not in USER_ALLOWED_COMMANDS:
        return f"权限不足：{full_command}"

    # mc 指令
    if command == "mc":
        return await _dispatch_mc_command(args)

    # tpa 指令
    if command == "tpa":
        return await _dispatch_tpa_command(args, level, player_name)

    # home 指令
    if command == "home":
        return await _dispatch_home_command(args, whisper_target=player_name)

    # git 指令
    if command == "git":
        return await _dispatch_git_command(args)

    return f"未知指令：{command}"


async def _dispatch_mc_command(args: list[str]) -> str:
    """处理 mc 子指令。"""
    if not args:
        return "干什么?!"

    sub = args[0]

    if sub == "start":
        _, message = await _start_js_process(persist_state=True)
        return message

    if sub == "stop":
        _, message = await _stop_js_process(persist_state=True)
        return message

    if sub == "status":
        state = _load_runtime_state()
        running = js_process is not None and js_process.returncode is None
        running_text = "运行中" if running else "未运行"
        should_start_text = "开启" if state.get("should_start") else "关闭"
        bot_text = str(state.get("bot_id") or "未设置")
        target_text = _format_target(state)
        pending_text = str(len(PENDING_BRIDGE_MESSAGES))

        return (
            "MC Bridge 状态：\n"
            f"- JS 进程: {running_text}\n"
            f"- 重启后自动恢复: {should_start_text}\n"
            f"- 推送 Bot: {bot_text}\n"
            f"- 推送目标: {target_text}\n"
            f"- 待补发消息: {pending_text}"
        )

    return "干什么?!"


async def _dispatch_git_command(args: list[str]) -> str:
    """处理 git 子指令。复用 auto_pull 的核心逻辑。"""
    if not args:
        return "你说得对，但是git是一款由Linus Torvalds开发的......"

    sub = args[0]

    if sub == "pull":
        return await _execute_git_pull()

    return "干什么?!"


async def _execute_git_pull() -> str:
    """执行 git pull 并返回格式化结果。当从 whisper 调用时不触发重启。"""
    import sys

    process = await asyncio.create_subprocess_shell(
        (
            'git -c '
            'url."https://gh-proxy.org/https://github.com/".insteadOf='
            '"https://github.com/" pull'
        ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    output = stdout.decode().strip() if stdout else ""
    err_output = stderr.decode().strip() if stderr else ""

    if process.returncode != 0:
        return f"更新失败 (错误码 {process.returncode})：\n{err_output}"

    # 获取 commit 日志
    git_log_process = await asyncio.create_subprocess_shell(
        'git log ORIG_HEAD..HEAD --pretty=format:"%h - %an : %s (%cr)"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    log_stdout, _ = await git_log_process.communicate()
    log_text = log_stdout.decode().strip() if log_stdout else ""

    result_parts = [output]
    if log_text:
        result_parts.append(log_text)

    return "\n".join(result_parts)


# ===== TPA 指令处理 =====

async def _dispatch_tpa_command(
    args: list[str],
    level: PermissionLevel,
    player_name: str | None = None,
) -> str:
    """处理 tpa 子指令。"""
    global TPA_STATE
    
    if not args:
        return "用法: tpa <on|off|status|back>"
    
    sub = args[0].lower()
    
    if sub == "on":
        if level < PermissionLevel.ADMIN:
            return "权限不足：需要管理员权限"
        
        TPA_STATE["enabled"] = True
        _save_tpa_state()
        
        # 推送状态到 JS
        await _push_tpa_state_to_js()
        
        return "TPA 自动接受已开启"
    
    if sub == "off":
        if level < PermissionLevel.ADMIN:
            return "权限不足：需要管理员权限"
        
        # 如果当前占用，先返回原位
        if TPA_STATE["occupied"]:
            try:
                await _execute_tpa_back()
            except Exception as e:
                logger.warning(f"TPA off 时返回原位失败: {e}")
                return f"关闭失败: {e}"
        
        TPA_STATE["enabled"] = False
        TPA_STATE["occupied"] = False
        TPA_STATE["occupied_by"] = None
        _save_tpa_state()
        
        # 推送状态到 JS
        await _push_tpa_state_to_js()
        
        return "TPA 自动接受已关闭"
    
    if sub == "status":
        enabled_text = "开启" if TPA_STATE["enabled"] else "关闭"
        occupied_text = f"是（{TPA_STATE['occupied_by']}）" if TPA_STATE["occupied"] else "否"
        
        return (
            f"TPA 状态：\n"
            f"- 自动接受: {enabled_text}\n"
            f"- 当前占用: {occupied_text}"
        )
    
    if sub == "back":
        if not TPA_STATE["occupied"]:
            return "当前没有占用，无需返回"
        
        # 权限检查：占用者本人或 admin
        is_occupier = (
            player_name is not None and
            TPA_STATE["occupied_by"] is not None and
            player_name.lower() == TPA_STATE["occupied_by"].lower()
        )
        
        if level < PermissionLevel.ADMIN and not is_occupier:
            return "权限不足：需要管理员权限或占用者本人"
        
        try:
            result = await _execute_tpa_back()
            return result
        except Exception as e:
            return f"返回失败: {e}"
    
    return "用法: tpa <on|off|status|back>"


async def _push_tpa_state_to_js() -> None:
    """推送当前 TPA 状态到 JS 侧。"""
    global js_process
    
    if js_process is None or js_process.returncode is not None:
        return
    
    encoded = _ipc_encode(IPC_ACTION_TPA_UPDATE_STATE, {
        "enabled": TPA_STATE["enabled"],
        "occupied": TPA_STATE["occupied"],
        "occupied_by": TPA_STATE["occupied_by"],
    })
    
    try:
        js_process.stdin.write(encoded.encode("utf-8"))
        await js_process.stdin.drain()
    except Exception as e:
        logger.error(f"推送 TPA 状态到 JS 失败: {e}")


async def _execute_tpa_back() -> str:
    """执行 TPA 返回操作：传送到 tpabackup 并删除。"""
    global TPA_STATE

    # 通过 IPC 请求 JS 执行 home tp
    tp_result = await _send_home_command("tp", "tpabackup")
    if not bool(tp_result.get("success")):
        error = tp_result.get("error") or "未知错误"
        raise RuntimeError(f"传送到 tpabackup 失败: {error}")

    # 删除 backup home
    remove_result = await _send_home_command("remove", "tpabackup")
    if not bool(remove_result.get("success")):
        error = remove_result.get("error") or "未知错误"
        raise RuntimeError(f"删除 tpabackup 失败: {error}")
    
    # 重置状态
    TPA_STATE["occupied"] = False
    TPA_STATE["occupied_by"] = None
    TPA_STATE["has_backup_home"] = False
    _save_tpa_state()
    
    # 推送状态到 JS
    await _push_tpa_state_to_js()

    return "已返回原位置"


# ===== Home 指令处理 =====

async def _dispatch_home_command(
    args: list[str],
    whisper_target: str | None = None,
) -> str:
    """处理 home 子指令。"""
    if not args:
        return "用法: home <list|tp|set|remove> [名称]"
    
    sub = args[0].lower()
    name = args[1] if len(args) > 1 else None
    
    if sub == "list":
        try:
            result = await _send_home_command("list", timeout=7.0, whisper_target=whisper_target)
            if result.get("direct_replied"):
                return ""
            if result.get("success"):
                homes = result.get("result", [])
                if isinstance(homes, list):
                    if not homes:
                        return "没有设置任何 home"
                    return f"Home 列表: {', '.join(homes)}"
                return f"Home 列表: {homes}"
            return f"获取 home 列表失败: {result.get('error', '未知错误')}"
        except asyncio.TimeoutError:
            return "获取 home 列表超时"
        except Exception as e:
            return f"获取 home 列表失败: {e}"
    
    if sub == "tp":
        if not name:
            # 没有指定名称时，返回 home 列表
            return await _dispatch_home_command(["list"], whisper_target=whisper_target)
        
        try:
            result = await _send_home_command("tp", name, timeout=10.0, whisper_target=whisper_target)
            if result.get("direct_replied"):
                return ""
            if result.get("success"):
                return f"已传送到 home: {name}"
            return f"传送失败: {result.get('error', '未知错误')}"
        except asyncio.TimeoutError:
            return "传送超时"
        except Exception as e:
            return f"传送失败: {e}"
    
    if sub == "set":
        if not name:
            return "用法: home set <名称>"
        
        try:
            result = await _send_home_command("set", name, timeout=5.0, whisper_target=whisper_target)
            if result.get("direct_replied"):
                return ""
            if result.get("success"):
                return f"已设置 home: {name}"
            return f"设置失败: {result.get('error', '未知错误')}"
        except asyncio.TimeoutError:
            return f"已发送设置 home 命令: {name}"
        except Exception as e:
            return f"设置失败: {e}"
    
    if sub == "remove":
        if not name:
            return "用法: home remove <名称>"
        
        try:
            result = await _send_home_command("remove", name, timeout=5.0, whisper_target=whisper_target)
            if result.get("direct_replied"):
                return ""
            if result.get("success"):
                return f"已删除 home: {name}"
            return f"删除失败: {result.get('error', '未知错误')}"
        except asyncio.TimeoutError:
            return f"已发送删除 home 命令: {name}"
        except Exception as e:
            return f"删除失败: {e}"
    
    return "用法: home <list|tp|set|remove> [名称]"


async def _send_home_command(
    command: str,
    name: str | None = None,
    timeout: float = 10.0,
    reply_to: str | None = None,
    whisper_target: str | None = None,
) -> dict:
    """发送 home 命令到 JS 并等待结果。"""
    global js_process
    
    if js_process is None or js_process.returncode is not None:
        raise RuntimeError("JS 进程未运行")

    normalized_reply_to = ""
    if isinstance(reply_to, str):
        normalized_reply_to = reply_to.strip()
    elif reply_to is not None:
        normalized_reply_to = str(reply_to).strip()

    if not normalized_reply_to:
        normalized_reply_to = f"home-{command}-{uuid4().hex[:12]}"
    
    # 创建 Future 用于等待结果
    wait_key = f"{normalized_reply_to}:{command}"
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    HOME_RESULT_PENDING[wait_key] = future
    
    try:
        # 发送命令
        encoded = _ipc_encode(IPC_ACTION_HOME_COMMAND, {
            "command": command,
            "name": name,
            "reply_to": normalized_reply_to,
            "whisper_target": whisper_target,
        })
        
        js_process.stdin.write(encoded.encode("utf-8"))
        await js_process.stdin.drain()
        
        # 等待结果
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    finally:
        # 清理
        HOME_RESULT_PENDING.pop(wait_key, None)


# ===== 持久化状态管理 =====

def _load_runtime_state() -> dict[str, bool | str | int | None]:
    state: dict[str, bool | str | int | None] = dict(RUNTIME_STATE_DEFAULT)
    if not RUNTIME_STATE_PATH.exists():
        return state

    try:
        loaded = json.loads(RUNTIME_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as error:
        logger.warning(f"读取持久化状态失败，使用默认状态: {error}")
        return state

    if not isinstance(loaded, dict):
        logger.warning("持久化状态格式无效，使用默认状态")
        return state

    should_start = loaded.get("should_start")
    if isinstance(should_start, bool):
        state["should_start"] = should_start

    bot_id = loaded.get("bot_id")
    if isinstance(bot_id, str) and bot_id:
        state["bot_id"] = bot_id

    target_type = loaded.get("target_type")
    if target_type in {"group", "private"}:
        state["target_type"] = target_type

    target_id = loaded.get("target_id")
    if isinstance(target_id, int) and target_id > 0:
        state["target_id"] = target_id
    elif isinstance(target_id, str) and target_id.isdigit():
        state["target_id"] = int(target_id)

    return state


def _save_runtime_state(
    *,
    should_start: bool,
    target: dict[str, str | int] | None = None,
) -> None:
    payload: dict[str, bool | str | int | None] = {
        "should_start": should_start,
        "bot_id": None,
        "target_type": None,
        "target_id": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # 启动但未提供新上下文时，保留上次的目标，以便重启后继续转发。
    if should_start and target is None:
        previous = _load_runtime_state()
        payload["bot_id"] = previous.get("bot_id")
        payload["target_type"] = previous.get("target_type")
        payload["target_id"] = previous.get("target_id")

    if target:
        payload["bot_id"] = str(target.get("bot_id"))
        payload["target_type"] = str(target.get("target_type"))
        target_id = target.get("target_id")
        payload["target_id"] = int(target_id) if target_id is not None else None

    try:
        RUNTIME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_STATE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as error:
        logger.error(f"写入持久化状态失败: {error}")


def _extract_target_from_event(bot: Bot, event: Event) -> dict[str, str | int] | None:
    group_id = getattr(event, "group_id", None)
    if isinstance(group_id, int):
        return {
            "bot_id": str(bot.self_id),
            "target_type": "group",
            "target_id": group_id,
        }

    user_id = getattr(event, "user_id", None)
    if isinstance(user_id, int):
        return {
            "bot_id": str(bot.self_id),
            "target_type": "private",
            "target_id": user_id,
        }

    return None


def _format_target(state: dict[str, bool | str | int | None]) -> str:
    target_type = state.get("target_type")
    target_id = state.get("target_id")

    if target_type == "group" and isinstance(target_id, int):
        return f"群聊 {target_id}"
    if target_type == "private" and isinstance(target_id, int):
        return f"私聊 {target_id}"
    return "未设置"


def _event_matches_runtime_target(bot: Bot, event: Event) -> bool:
    # 优先使用内存中的活跃上下文，避免每条消息都读取磁盘状态文件。
    if active_bot and active_event:
        if str(bot.self_id) != str(active_bot.self_id):
            return False
        active_target = _extract_target_from_event(active_bot, active_event)
        if active_target:
            target_type = active_target.get("target_type")
            target_id = active_target.get("target_id")
            group_id = getattr(event, "group_id", None)
            user_id = getattr(event, "user_id", None)

            if target_type == "group":
                return isinstance(group_id, int) and group_id == target_id
            if target_type == "private":
                return (not isinstance(group_id, int)) and isinstance(user_id, int) and user_id == target_id

    state = _load_runtime_state()
    bot_id = state.get("bot_id")
    target_type = state.get("target_type")
    target_id = state.get("target_id")

    if isinstance(bot_id, str) and bot_id and str(bot.self_id) != bot_id:
        return False
    if not isinstance(target_id, int):
        return False

    group_id = getattr(event, "group_id", None)
    user_id = getattr(event, "user_id", None)

    if target_type == "group":
        return isinstance(group_id, int) and group_id == target_id
    if target_type == "private":
        return (not isinstance(group_id, int)) and isinstance(user_id, int) and user_id == target_id
    return False


# ===== IPC 通信 =====

async def _write_ipc_to_js(action: str, data: dict[str, object]) -> bool:
    """通过统一 IPC 格式向 JS 进程发送消息。"""
    process = js_process
    if process is None or process.returncode is not None:
        return False

    if process.stdin is None:
        logger.warning("JS stdin 不可用，无法写入 IPC 消息")
        return False

    try:
        line = _ipc_encode(action, data)
        process.stdin.write(line.encode("utf-8"))
        await process.stdin.drain()
        return True
    except Exception as error:
        logger.error(f"写入 JS stdin 失败: {error}")
        return False


async def _send_whisper_reply(player_name: str, message: str) -> bool:
    """通过 IPC 向 JS 发送 whisper 回复。"""
    return await _write_ipc_to_js(IPC_ACTION_WHISPER_REPLY, {
        "target_player": player_name,
        "msg": message,
    })


async def _send_bridge_message(message: str) -> None:
    if not message:
        return

    delivered = await _try_send_bridge_message(message)
    if delivered:
        return

    PENDING_BRIDGE_MESSAGES.append(message)
    logger.warning(
        "桥接消息暂存：OneBot 尚未就绪或目标不可用，等待连接恢复后补发"
    )


async def _try_send_bridge_message(message: str) -> bool:
    if not message:
        return True

    if active_bot and active_event:
        try:
            await active_bot.send(active_event, message)
            return True
        except Exception as error:
            logger.error(f"通过事件上下文发送消息失败: {error}")

    state = _load_runtime_state()
    bot_id = state.get("bot_id")
    target_type = state.get("target_type")
    target_id = state.get("target_id")

    if not isinstance(bot_id, str) or target_type not in {"group", "private"}:
        return False
    if not isinstance(target_id, int):
        return False

    bot_instance = get_bots().get(bot_id)
    if bot_instance is None:
        return False

    try:
        if target_type == "group":
            await bot_instance.call_api("send_group_msg", group_id=target_id, message=message)
        else:
            await bot_instance.call_api("send_private_msg", user_id=target_id, message=message)
        return True
    except Exception as error:
        logger.error(f"发送桥接消息失败: {error}")
        return False


async def _flush_pending_bridge_messages() -> None:
    if not PENDING_BRIDGE_MESSAGES:
        return

    pending = list(PENDING_BRIDGE_MESSAGES)
    PENDING_BRIDGE_MESSAGES.clear()
    sent_count = 0

    for index, pending_message in enumerate(pending):
        delivered = await _try_send_bridge_message(pending_message)
        if delivered:
            sent_count += 1
            continue

        for remaining_message in pending[index:]:
            PENDING_BRIDGE_MESSAGES.append(remaining_message)
        break

    if sent_count > 0:
        logger.info(f"已补发桥接消息 {sent_count} 条")


# ===== JS 进程管理 =====

async def _start_js_process(
    bot: Bot | None = None,
    event: Event | None = None,
    *,
    persist_state: bool = True,
) -> tuple[bool, str]:
    global active_bot, active_event, js_process, js_stderr_lines, js_stop_requested

    if js_process and js_process.returncode is None:
        if bot and event:
            active_bot = bot
            active_event = event
            if persist_state:
                _save_runtime_state(
                    should_start=True,
                    target=_extract_target_from_event(bot, event),
                )
            return False, "JS 脚本已经在运行中，已更新消息推送目标"
        return False, "JS 脚本已经在运行中"

    if bot and event:
        active_bot = bot
        active_event = event

    js_stop_requested = False
    js_stderr_lines = []
    js_path = Path(__file__).parent / "src/index.js"

    if JS_START_DELAY_SECONDS > 0:
        logger.info(f"将在 {JS_START_DELAY_SECONDS} 秒后启动 JS 进程")
        await asyncio.sleep(JS_START_DELAY_SECONDS)

    try:
        js_process = await asyncio.create_subprocess_exec(
            "node",
            str(js_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        js_process = None
        return False, "启动失败：未找到 Node.js，请先确认 node 命令可用"
    except Exception as error:
        js_process = None
        logger.error(f"启动 JS 进程失败: {error}")
        return False, f"启动失败：{error}"

    asyncio.create_task(listen_to_js(), name="mineflayer-js-listener")
    if persist_state:
        _save_runtime_state(
            should_start=True,
            target=_extract_target_from_event(bot, event) if bot and event else None,
        )
    return True, "开启链接中..."


async def _stop_js_process(*, persist_state: bool = True) -> tuple[bool, str]:
    global js_process, js_stop_requested

    process = js_process
    if process is None or process.returncode is not None:
        js_process = None
        if persist_state:
            _save_runtime_state(should_start=False)
        return False, "JS 脚本没有在运行"

    js_stop_requested = True
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
    finally:
        if js_process is process:
            js_process = None

    if persist_state:
        _save_runtime_state(should_start=False)
    return True, "已停止连接"


driver = get_driver()


@driver.on_startup
async def _restore_js_process_on_startup() -> None:
    global TPA_STATE
    
    # 加载 TPA 状态
    TPA_STATE = _load_tpa_state()
    logger.info(f"TPA state loaded: enabled={TPA_STATE['enabled']}, occupied={TPA_STATE['occupied']}")
    
    state = _load_runtime_state()
    if not state["should_start"]:
        return

    logger.info("检测到自动恢复已开启，将在 OneBot 连接后启动 JS 进程")


@driver.on_bot_connect
async def _restore_target_bot_on_connect(bot: Bot) -> None:
    global active_bot

    state = _load_runtime_state()
    saved_bot_id = state.get("bot_id")
    can_bind = (
        not isinstance(saved_bot_id, str)
        or not saved_bot_id
        or str(bot.self_id) == saved_bot_id
    )

    if can_bind:
        active_bot = bot
        logger.info(f"已恢复消息推送 Bot: {bot.self_id}")

        should_start = bool(state.get("should_start"))
        is_running = js_process is not None and js_process.returncode is None
        if should_start and not is_running:
            started, message = await _start_js_process(persist_state=False)
            if started:
                logger.info("已在 OneBot 连接后自动恢复 JS 进程")
            else:
                logger.warning(f"OneBot 连接后自动恢复 JS 进程失败: {message}")

        await _flush_pending_bridge_messages()


@driver.on_shutdown
async def _stop_js_process_on_shutdown() -> None:
    if js_process is None or js_process.returncode is not None:
        return

    logger.info("Bot 正在关闭，停止 JS 子进程")
    await _stop_js_process(persist_state=False)


# ===== NoneBot 指令处理器（QQ 端）=====

@mc.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    start = args.extract_plain_text().strip()

    if start == "start":
        started, message = await _start_js_process(bot=bot, event=event, persist_state=True)
        target_emoji = EMOJI_STATUS_SUCCESS if started else EMOJI_STATUS_FAILED
        await mc.send(message)
        await set_status_emoji(bot, message_id, target_emoji)

    elif start == "stop":
        stopped, message = await _stop_js_process(persist_state=True)
        target_emoji = EMOJI_STATUS_SUCCESS if stopped else EMOJI_STATUS_FAILED
        await mc.send(message)
        await set_status_emoji(bot, message_id, target_emoji)

    elif start == "status":
        result = await dispatch_command("mc", ["status"], PermissionLevel.ADMIN)
        await mc.send(result)
        await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)

    else:
        await mc.send("干什么?!")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)


@tpa_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    arg_text = args.extract_plain_text().strip()
    arg_list = arg_text.split() if arg_text else []

    result = await dispatch_command("tpa", arg_list, PermissionLevel.ADMIN)
    await tpa_cmd.send(result)

    success = "失败" not in result and "不足" not in result
    target_emoji = EMOJI_STATUS_SUCCESS if success else EMOJI_STATUS_FAILED
    await set_status_emoji(bot, message_id, target_emoji)


@home_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    arg_text = args.extract_plain_text().strip()
    arg_list = arg_text.split() if arg_text else []

    result = await dispatch_command("home", arg_list, PermissionLevel.ADMIN)
    await home_cmd.send(result)

    success = "失败" not in result and "超时" not in result
    target_emoji = EMOJI_STATUS_SUCCESS if success else EMOJI_STATUS_FAILED
    await set_status_emoji(bot, message_id, target_emoji)


@bridge_input.handle()
async def _(bot: Bot, event: Event):
    process = js_process
    if process is None or process.returncode is not None:
        return

    if not _event_matches_runtime_target(bot, event):
        return

    sender_id = getattr(event, "user_id", None)
    if sender_id is not None and str(sender_id) == str(bot.self_id):
        return

    plain_text = ""
    if hasattr(event, "get_plaintext"):
        plain_text = event.get_plaintext().strip()
    if not plain_text:
        return

    if plain_text.startswith("/mc") or plain_text.startswith("/connect"):
        return
    if plain_text.startswith("[插件服]>>"):
        return

    # 统一 IPC 格式
    sent = await _write_ipc_to_js(IPC_ACTION_QQ_MESSAGE, {
        "group_id": getattr(event, "group_id", None) or "",
        "sender_id": str(sender_id or ""),
        "msg": plain_text,
    })
    if not sent:
        logger.debug("本条消息未写入 JS stdin")


# ==========================================
# 全局缓存 (核心思路)
# ==========================================
_LANG_CACHE = {
    "zh_cn": {},
    "en_us": {}
}


def _load_language_file(file_path):
    """内部函数：读取 JSON 文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"警告：无法加载语言文件 '{file_path}'。原因: {e}")
        return {}


# ===== JS stdout/stderr 监听 =====

async def read_stdout(process: asyncio.subprocess.Process):
    """监听 JS stdout，解析统一 IPC 消息并分发处理。"""
    while process.returncode is None:
        line = await process.stdout.readline()
        if not line:
            break
        try:
            decoded_line = line.decode().strip()
            if not decoded_line:
                continue

            envelope = _ipc_decode(decoded_line)
            if envelope is None:
                # 非 IPC 格式的普通输出
                logger.debug(f"JS 普通输出: {decoded_line}")
                continue

            action = envelope["action"]
            data = envelope.get("data", {})

            if action == IPC_ACTION_MC_MESSAGE:
                await _handle_mc_message(data)

            elif action == IPC_ACTION_WHISPER_COMMAND:
                await _handle_whisper_command(data)

            elif action == IPC_ACTION_PLAYER_LIST:
                await _handle_player_list(data)

            elif action == IPC_ACTION_TPA_OCCUPIED:
                await _handle_tpa_occupied(data)

            elif action == IPC_ACTION_TPA_REQUEST_DETECTED:
                await _handle_tpa_request_detected(data)

            elif action == IPC_ACTION_HOME_RESULT:
                await _handle_home_result(data)

            elif action == IPC_ACTION_REQUEST_TPA_STATE:
                await _push_tpa_state_to_js()

            elif action == IPC_ACTION_TPA_NOTIFICATION:
                msg = data.get("msg", "")
                if msg:
                    await _send_bridge_message(f"[插件服]>>{msg}")

            else:
                logger.warning(f"JS 发来未知 IPC action: {action}")

        except Exception as e:
            logger.error(f"解析 JS 数据失败: {e}")


async def _handle_mc_message(received_msg: dict[str, object]) -> None:
    """处理来自 JS 的 mc_message：翻译并转发到 QQ。"""
    output = ""

    if received_msg.get("type") == "whisper":
        # whisper 已在 JS 端处理，不转发到 QQ
        return

    translate = received_msg.get("translate")
    if translate:
        params = received_msg.get("params", [])
        match len(params):
            case 1:
                output = translate_mc_key(
                    translate, "zh_cn",
                    params[0]["name"] if isinstance(params[0], dict) else str(params[0]),
                )
            case 2:
                output = translate_mc_key(
                    translate, "zh_cn",
                    params[0]["name"] if isinstance(params[0], dict) else str(params[0]),
                    params[1]["name"] if isinstance(params[1], dict) else str(params[1]),
                )
            case 3:
                output = translate_mc_key(
                    translate, "zh_cn",
                    params[0]["name"] if isinstance(params[0], dict) else str(params[0]),
                    params[1]["name"] if isinstance(params[1], dict) else str(params[1]),
                    params[2]["name"] if isinstance(params[2], dict) else str(params[2]),
                )
    elif received_msg.get("type") == "chat":
        output = f"{received_msg.get('text', '')}"
    else:
        return

    if output:
        await _send_bridge_message(f"[插件服]>>{output}")


async def _handle_whisper_command(data: dict[str, object]) -> None:
    """处理来自 JS 的 whisper_command：执行指令并 whisper 回复。"""
    player_name = data.get("player_name", "")
    command = data.get("command", "")
    args = data.get("args", [])
    permission_level = data.get("permission_level", "user")

    if not player_name or not command:
        return

    logger.info(f"MC whisper 指令: [{permission_level}] {player_name} -> {command} {args}")

    try:
        result = await dispatch_command(command, args, permission_level, player_name)
    except Exception as e:
        result = f"指令执行失败：{e}"
        logger.error(f"whisper 指令执行异常: {e}")

    if isinstance(result, str) and not result.strip():
        return

    # 通过 MC whisper 回复
    await _send_whisper_reply(player_name, result)


async def _handle_player_list(data: dict[str, object]) -> None:
    """处理来自 JS 的 player_list：记录在线玩家快照。"""
    from .player_tracker import record_snapshot

    players = data.get("players", [])
    timestamp = data.get("timestamp", "")
    bot_username = data.get("bot_username", "")

    if not isinstance(players, list):
        return

    try:
        record_snapshot(players, str(timestamp), bot_username=str(bot_username))
    except Exception as e:
        logger.error(f"记录玩家快照失败: {e}")


async def _handle_tpa_occupied(data: dict[str, object]) -> None:
    """处理来自 JS 的 tpa_occupied：更新 TPA 占用状态。"""
    global TPA_STATE
    
    occupied_by = data.get("occupied_by", "")
    tpa_type = data.get("tpa_type", "")
    
    TPA_STATE["occupied"] = True
    TPA_STATE["occupied_by"] = occupied_by
    TPA_STATE["has_backup_home"] = True
    _save_tpa_state()
    
    logger.info(f"TPA occupied by {occupied_by} (type: {tpa_type})")


async def _handle_tpa_request_detected(data: dict[str, object]) -> None:
    """处理来自 JS 的 tpa_request_detected：记录 TPA 请求日志。"""
    requester = data.get("requester", "")
    tpa_type = data.get("type", "")
    
    logger.info(f"TPA request detected: {requester} ({tpa_type})")


async def _handle_home_result(data: dict[str, object]) -> None:
    """处理来自 JS 的 home_result：将结果传递给等待的 Future。"""
    reply_to = data.get("reply_to", "")
    command = data.get("command", "")

    if not isinstance(reply_to, str):
        reply_to = "" if reply_to is None else str(reply_to)
    if not isinstance(command, str):
        command = "" if command is None else str(command)

    reply_to = reply_to.strip()
    command = command.strip()

    if not command:
        logger.warning("收到无 command 的 home_result，忽略")
        return
    
    # 构造等待键
    wait_key = f"{reply_to}:{command}" if reply_to else command
    
    if wait_key in HOME_RESULT_PENDING:
        future = HOME_RESULT_PENDING.pop(wait_key)
        if not future.done():
            future.set_result(data)
    else:
        # 没有等待的 Future，直接记录日志
        success = data.get("success", False)
        result = data.get("result")
        error = data.get("error")
        
        if success:
            logger.info(f"Home command '{command}' succeeded: {result}")
        else:
            logger.warning(f"Home command '{command}' failed: {error}")


async def read_stderr(process: asyncio.subprocess.Process):
    global js_stderr_lines
    while process.returncode is None:
        err = await process.stderr.readline()
        if not err:
            break
        err_text = err.decode().strip()
        logger.error(f"JS 错误输出: {err_text}")
        if err_text:
            js_stderr_lines.append(err_text)
            # 只保留最后 8 行，避免消息过长
            js_stderr_lines = js_stderr_lines[-8:]

async def listen_to_js():
    global js_process, js_stop_requested, js_stderr_lines
    process = js_process
    if process is None:
        return

    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 拼接出 zh_cn.json 和 en_us.json 的绝对路径
    zh_cn_path = os.path.join(current_dir, "../../configs/zh_cn.json")
    en_us_path = os.path.join(current_dir, "../../configs/en_us.json")
    # 模块加载时，立即把数据读进内存
    _LANG_CACHE["zh_cn"] = _load_language_file(zh_cn_path)
    _LANG_CACHE["en_us"] = _load_language_file(en_us_path)
    logger.info("✅ MC 语言包已成功加载到内存！")

    await asyncio.gather(
        read_stdout(process),
        read_stderr(process),
        return_exceptions=True
    )

    await process.wait()
    return_code = process.returncode

    # 仅在非手动停止且退出码非 0 时，主动推送告警消息。
    if (not js_stop_requested) and return_code not in (None, 0):
        err_summary = "\n".join(js_stderr_lines).strip()
        if not err_summary:
            err_summary = "(无 stderr 输出)"
        alert = f"[插件服] JS 进程异常退出，退出码: {return_code}\n最近错误输出：\n{err_summary}"

        await _send_bridge_message(alert)

    js_stop_requested = False
    if js_process is process:
        js_process = None


# ===== MC 翻译 =====

def translate_mc_key(key, lang="zh_cn", *args):
    """
    根据语言字典翻译键名，并替换其中的占位符。
    """
    # 指定语言字典 (直接从内存缓存中拿)
    lang_data = _LANG_CACHE.get(lang, _LANG_CACHE["zh_cn"])

    # 从字典中获取原始翻译文本
    raw_text = lang_data.get(key)

    # 如果找不到对应的键，直接返回提示
    if not raw_text:
        return f"[{key} 未找到]"

    processed_args = []
    for arg in args:
        # 如果参数是字符串，并且这个字符串在语言包里能找到对应的翻译
        if isinstance(arg, str) and arg in lang_data:
            # 就把它替换成翻译后的中文/英文
            processed_args.append(lang_data[arg])
        else:
            # 否则（比如是普通玩家名字、数字等），保持原样
            processed_args.append(arg)

    # 将 MC 的占位符 (%s, %d, %1$s, %2$s 等) 替换为 Python 的 {}
    formatted_text = re.sub(r'%(\d+\$)?[sd]', ' {} ', raw_text)

    # 填入变量
    try:
        return formatted_text.format(*processed_args)
    except IndexError:
        # 如果传入的 args 数量少于占位符数量，为了防止报错，原样返回带有占位符的文本
        return raw_text