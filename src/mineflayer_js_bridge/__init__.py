from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import nonebot
import websockets
from nonebot import get_bots, get_driver, get_plugin_config, logger, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from websockets.exceptions import ConnectionClosed

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
    description="MC WebSocket 连接管理",
    usage="mc <connect|disconnect|logout|status>",
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

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
LEGACY_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
RUNTIME_STATE_PATH = DATA_DIR / "mineflayer_ws_bridge.runtime.json"
LEGACY_RUNTIME_STATE_PATH = LEGACY_CONFIGS_DIR / "mineflayer_js_bridge.runtime.json"
RUNTIME_STATE_DEFAULT: dict[str, bool | str | int | None] = {
    "should_connect": False,
    "mc_bot_id": None,
    "mc_bot_state": None,
    "onebot_id": None,
    "target_type": None,
    "target_id": None,
    "account_preset": None,
    "server_preset": None,
}

active_bot: Bot | None = None
active_event: Event | None = None
ws_connection: Any | None = None
ws_reader_task: asyncio.Task[None] | None = None
player_poll_task: asyncio.Task[None] | None = None
authenticated = False
current_bot_id: str | None = None
current_bot_state = "offline"
pending_replies: dict[str, asyncio.Future[dict[str, Any]]] = {}
connection_lock = asyncio.Lock()
pending_bridge_messages: deque[str] = deque(maxlen=50)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_msg_id(kind: str) -> str:
    return f"msg_{_now_ms()}_{kind}_{uuid4().hex[:4]}"


def _ws_url() -> str:
    return f"ws://{config.mineflayer_ws_host}:{config.mineflayer_ws_port}"


def _is_ws_connected() -> bool:
    return ws_connection is not None


def _load_runtime_state() -> dict[str, bool | str | int | None]:
    state: dict[str, bool | str | int | None] = dict(RUNTIME_STATE_DEFAULT)
    path = RUNTIME_STATE_PATH if RUNTIME_STATE_PATH.exists() else LEGACY_RUNTIME_STATE_PATH
    if not path.exists():
        return state

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        logger.warning(f"读取 WebSocket 桥接状态失败，使用默认状态: {error}")
        return state

    if not isinstance(loaded, dict):
        logger.warning("WebSocket 桥接状态格式无效，使用默认状态")
        return state

    state["should_connect"] = bool(
        loaded.get("should_connect", loaded.get("should_start", False))
    )

    mc_bot_id = loaded.get("mc_bot_id") or loaded.get("bot_id")
    if isinstance(mc_bot_id, str) and mc_bot_id:
        state["mc_bot_id"] = mc_bot_id

    mc_bot_state = loaded.get("mc_bot_state")
    if isinstance(mc_bot_state, str) and mc_bot_state:
        state["mc_bot_state"] = mc_bot_state

    onebot_id = loaded.get("onebot_id")
    if isinstance(onebot_id, str) and onebot_id:
        state["onebot_id"] = onebot_id

    target_type = loaded.get("target_type")
    if target_type in {"group", "private"}:
        state["target_type"] = target_type

    target_id = loaded.get("target_id")
    if isinstance(target_id, int) and target_id > 0:
        state["target_id"] = target_id
    elif isinstance(target_id, str) and target_id.isdigit():
        state["target_id"] = int(target_id)

    for key in ("account_preset", "server_preset"):
        value = loaded.get(key)
        if isinstance(value, int) and value > 0:
            state[key] = value
        elif isinstance(value, str) and value.isdigit():
            state[key] = int(value)

    if path == LEGACY_RUNTIME_STATE_PATH:
        _save_runtime_state(**state)

    return state


def _save_runtime_state(
    *,
    should_connect: bool,
    mc_bot_id: str | None = None,
    mc_bot_state: str | None = None,
    onebot_id: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    account_preset: int | None = None,
    server_preset: int | None = None,
    clear_current_bot: bool = False,
) -> None:
    previous = _load_runtime_state() if RUNTIME_STATE_PATH.exists() else dict(RUNTIME_STATE_DEFAULT)
    payload: dict[str, bool | str | int | None] = {
        "should_connect": should_connect,
        "mc_bot_id": None
        if clear_current_bot
        else mc_bot_id
        if mc_bot_id is not None
        else previous.get("mc_bot_id"),
        "mc_bot_state": mc_bot_state if mc_bot_state is not None else previous.get("mc_bot_state"),
        "onebot_id": onebot_id if onebot_id is not None else previous.get("onebot_id"),
        "target_type": target_type if target_type is not None else previous.get("target_type"),
        "target_id": target_id if target_id is not None else previous.get("target_id"),
        "account_preset": account_preset
        if account_preset is not None
        else previous.get("account_preset"),
        "server_preset": server_preset
        if server_preset is not None
        else previous.get("server_preset"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        RUNTIME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_STATE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as error:
        logger.error(f"写入 WebSocket 桥接状态失败: {error}")


def _extract_target_from_event(bot: Bot, event: Event) -> dict[str, str | int] | None:
    group_id = getattr(event, "group_id", None)
    if isinstance(group_id, int):
        return {
            "onebot_id": str(bot.self_id),
            "target_type": "group",
            "target_id": group_id,
        }

    user_id = getattr(event, "user_id", None)
    if isinstance(user_id, int):
        return {
            "onebot_id": str(bot.self_id),
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
    if active_bot and active_event:
        if str(bot.self_id) != str(active_bot.self_id):
            return False
        active_target = _extract_target_from_event(active_bot, active_event)
        if active_target:
            return _event_matches_target(event, active_target)

    state = _load_runtime_state()
    onebot_id = state.get("onebot_id")
    if isinstance(onebot_id, str) and onebot_id and str(bot.self_id) != onebot_id:
        return False
    return _event_matches_target(event, state)


def _event_matches_target(event: Event, target: dict[str, Any]) -> bool:
    target_type = target.get("target_type")
    target_id = target.get("target_id")
    if not isinstance(target_id, int):
        return False

    group_id = getattr(event, "group_id", None)
    user_id = getattr(event, "user_id", None)
    if target_type == "group":
        return isinstance(group_id, int) and group_id == target_id
    if target_type == "private":
        return (not isinstance(group_id, int)) and isinstance(user_id, int) and user_id == target_id
    return False


def _message_result_text(result: Any) -> str:
    if result is None:
        return "（无返回结果）"
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("reply", "message", "text", "state"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(result, ensure_ascii=False)
    return str(result)


async def _send_payload(payload: dict[str, Any]) -> None:
    if ws_connection is None:
        msg = "WebSocket 未连接"
        raise RuntimeError(msg)
    await ws_connection.send(json.dumps(payload, ensure_ascii=False))


async def _send_request(
    message_type: str,
    data: dict[str, Any] | None = None,
    *,
    bot_id: str | None = None,
    extra: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    msg_id = _new_msg_id(message_type)
    payload: dict[str, Any] = {
        "type": message_type,
        "timestamp": _now_ms(),
        "need_reply": True,
        "msg_id": msg_id,
        "data": data or {},
        "extra": extra or {},
    }
    if bot_id:
        payload["bot_id"] = bot_id

    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()
    pending_replies[msg_id] = future
    try:
        await _send_payload(payload)
        reply = await asyncio.wait_for(
            future,
            timeout=timeout or config.mineflayer_ws_request_timeout,
        )
    finally:
        pending_replies.pop(msg_id, None)

    reply_data = reply.get("data", {})
    if not isinstance(reply_data, dict):
        msg = "WebSocket 回复格式无效"
        raise RuntimeError(msg)

    status = reply_data.get("status")
    result = reply_data.get("result")
    if status == "error":
        if isinstance(result, dict):
            error_message = result.get("error_message") or result.get("error_type")
            raise RuntimeError(str(error_message or result))
        raise RuntimeError(str(result or "请求失败"))
    return reply


async def _send_reply(msg_id: str, result: Any = None, *, status: str = "success") -> None:
    payload = {
        "type": "reply",
        "timestamp": _now_ms(),
        "need_reply": False,
        "data": {
            "msg_id": msg_id,
            "status": status,
            "result": result,
        },
    }
    await _send_payload(payload)


async def _authenticate() -> None:
    global authenticated
    reply = await _send_request("auth", {"token": config.mineflayer_ws_token})
    result = reply.get("data", {}).get("result")
    authenticated = bool(result.get("authenticated")) if isinstance(result, dict) else True


async def _select_or_login_bot(account_preset: int, server_preset: int) -> str:
    state = _load_runtime_state()
    saved_bot_id = state.get("mc_bot_id")
    if isinstance(saved_bot_id, str) and saved_bot_id:
        try:
            reply = await _send_request("bot_info", bot_id=saved_bot_id)
            result = reply.get("data", {}).get("result", {})
            if isinstance(result, dict):
                _set_current_bot(saved_bot_id, str(result.get("state") or "unknown"))
                return saved_bot_id
        except Exception as error:
            logger.info(f"恢复已有 MC bot 失败，将尝试列表/登录: {error}")

    try:
        reply = await _send_request("bot_list")
        result = reply.get("data", {}).get("result", {})
        bots = result.get("bots", []) if isinstance(result, dict) else []
        if isinstance(bots, list) and bots:
            first_bot = next((bot for bot in bots if isinstance(bot, dict)), None)
            if first_bot:
                bot_id = str(first_bot.get("bot_id") or "")
                if bot_id:
                    _set_current_bot(bot_id, str(first_bot.get("state") or "unknown"))
                    return bot_id
    except Exception as error:
        logger.info(f"查询 MC bot 列表失败，将尝试登录: {error}")

    reply = await _send_request(
        "login_preset",
        {"account": account_preset, "server": server_preset},
        timeout=max(config.mineflayer_ws_request_timeout, 60),
    )
    result = reply.get("data", {}).get("result", {})
    if not isinstance(result, dict) or not result.get("bot_id"):
        msg = "login_preset 成功但未返回 bot_id"
        raise RuntimeError(msg)

    bot_id = str(result["bot_id"])
    _set_current_bot(bot_id, str(result.get("state") or "online"))
    return bot_id


def _set_current_bot(bot_id: str | None, state: str | None = None) -> None:
    global current_bot_id, current_bot_state
    current_bot_id = bot_id
    if state:
        current_bot_state = state


async def _connect_ws(
    *,
    bot: Bot | None = None,
    event: Event | None = None,
    account_preset: int | None = None,
    server_preset: int | None = None,
    persist_state: bool = True,
) -> tuple[bool, str]:
    global active_bot, active_event, authenticated, player_poll_task
    global ws_connection, ws_reader_task

    async with connection_lock:
        if bot and event:
            active_bot = bot
            active_event = event

        account = account_preset or config.mineflayer_ws_account_preset
        server = server_preset or config.mineflayer_ws_server_preset

        if _is_ws_connected():
            if persist_state:
                _persist_connection_state(
                    should_connect=True,
                    bot=bot,
                    event=event,
                    account_preset=account,
                    server_preset=server,
                )
            return False, "WebSocket 已连接，已更新消息推送目标"

        try:
            ws_connection = await websockets.connect(
                _ws_url(),
                open_timeout=config.mineflayer_ws_request_timeout,
                close_timeout=5,
            )
            ws_reader_task = asyncio.create_task(
                _read_ws_messages(),
                name="mineflayer-ws-reader",
            )
            await _authenticate()
            bot_id = await _select_or_login_bot(account, server)
        except Exception as error:
            await _close_ws_connection(persist_state=False)
            logger.error(f"连接 Mineflayer WebSocket 失败: {error}")
            return False, f"连接失败：{error}"

        if player_poll_task is None or player_poll_task.done():
            player_poll_task = asyncio.create_task(
                _poll_players_loop(),
                name="mineflayer-player-poller",
            )

        if persist_state:
            _persist_connection_state(
                should_connect=True,
                bot=bot,
                event=event,
                account_preset=account,
                server_preset=server,
            )

        await _flush_pending_bridge_messages()
        return True, f"已连接 WebSocket，并绑定 MC bot: {bot_id}"


def _persist_connection_state(
    *,
    should_connect: bool,
    bot: Bot | None = None,
    event: Event | None = None,
    account_preset: int | None = None,
    server_preset: int | None = None,
) -> None:
    target = _extract_target_from_event(bot, event) if bot and event else {}
    _save_runtime_state(
        should_connect=should_connect,
        mc_bot_id=current_bot_id,
        mc_bot_state=current_bot_state,
        onebot_id=str(target.get("onebot_id")) if target else None,
        target_type=str(target.get("target_type")) if target else None,
        target_id=int(target["target_id"]) if target and target.get("target_id") else None,
        account_preset=account_preset,
        server_preset=server_preset,
    )


async def _close_ws_connection(*, persist_state: bool = True) -> tuple[bool, str]:
    global authenticated, ws_connection, ws_reader_task, player_poll_task

    current_task = asyncio.current_task()
    connection = ws_connection
    was_connected = connection is not None
    ws_connection = None
    authenticated = False

    if player_poll_task is not None:
        player_poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await player_poll_task
        player_poll_task = None

    if ws_reader_task is not None and ws_reader_task is not current_task:
        ws_reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ws_reader_task
        ws_reader_task = None
    elif ws_reader_task is current_task:
        ws_reader_task = None

    if connection is not None:
        with contextlib.suppress(Exception):
            await connection.close()

    for future in pending_replies.values():
        if not future.done():
            future.set_exception(RuntimeError("WebSocket 连接已关闭"))
    pending_replies.clear()

    if persist_state:
        _save_runtime_state(should_connect=False, mc_bot_id=current_bot_id, mc_bot_state=current_bot_state)

    return was_connected, "已断开 WebSocket 连接" if was_connected else "WebSocket 未连接"


async def _logout_current_bot() -> tuple[bool, str]:
    global current_bot_id, current_bot_state
    if not _is_ws_connected():
        return False, "WebSocket 未连接"
    if not current_bot_id:
        return False, "当前未绑定 MC bot"

    try:
        await _send_request("logout", bot_id=current_bot_id)
    except Exception as error:
        return False, f"logout 失败：{error}"

    old_bot_id = current_bot_id
    current_bot_id = None
    current_bot_state = "stopped"
    _save_runtime_state(
        should_connect=False,
        mc_bot_state=current_bot_state,
        clear_current_bot=True,
    )
    return True, f"已退出 MC bot: {old_bot_id}"


async def _read_ws_messages() -> None:
    global current_bot_state

    try:
        while ws_connection is not None:
            raw_message = await ws_connection.recv()
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                logger.warning(f"收到非 JSON WebSocket 消息: {raw_message}")
                continue

            if not isinstance(message, dict):
                logger.warning(f"收到无效 WebSocket 消息: {message}")
                continue

            await _handle_ws_message(message)
    except asyncio.CancelledError:
        raise
    except ConnectionClosed as error:
        logger.warning(f"Mineflayer WebSocket 连接关闭: {error}")
    except Exception as error:
        logger.exception(f"Mineflayer WebSocket 读取失败: {error}")
    finally:
        if ws_connection is not None:
            current_bot_state = "offline"
            await _close_ws_connection(persist_state=False)


async def _handle_ws_message(message: dict[str, Any]) -> None:
    message_type = message.get("type")

    if message_type == "reply":
        data = message.get("data", {})
        msg_id = data.get("msg_id") if isinstance(data, dict) else None
        if isinstance(msg_id, str):
            future = pending_replies.get(msg_id)
            if future is not None and not future.done():
                future.set_result(message)
                return
        logger.debug(f"收到未匹配的 WebSocket reply: {message}")
        return

    if message_type == "msg":
        await _handle_mc_message(message)
        return

    if message_type == "event":
        await _handle_server_event(message)
        return

    if message_type == "command":
        await _handle_server_command(message)
        return

    if message_type == "error":
        await _handle_server_error(message)
        return

    logger.warning(f"收到未知 WebSocket type: {message_type}")


async def _handle_mc_message(message: dict[str, Any]) -> None:
    data = message.get("data", {})
    if not isinstance(data, dict):
        return

    if data.get("position") == "private_outgoing":
        return

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return

    await _send_bridge_message(f"{config.mineflayer_ws_mc_prefix}{text}")


async def _handle_server_event(message: dict[str, Any]) -> None:
    data = message.get("data", {})
    if not isinstance(data, dict):
        return

    event_type = data.get("event_type")
    event_data = data.get("event_data", {})
    if event_type == "bot.status" and isinstance(event_data, dict):
        state = event_data.get("state")
        if isinstance(state, str):
            _set_current_bot(message.get("bot_id") or current_bot_id, state)
            _save_runtime_state(
                should_connect=True,
                mc_bot_id=current_bot_id,
                mc_bot_state=current_bot_state,
            )
    elif event_type in {"tpa.notification", "system.notice"} and isinstance(event_data, dict):
        notice = event_data.get("message")
        if isinstance(notice, str) and notice:
            await _send_bridge_message(f"{config.mineflayer_ws_mc_prefix}{notice}")
    elif event_type == "tpa.request_detected":
        logger.info(f"TPA request detected: {event_data}")
    else:
        logger.debug(f"收到服务端事件: {event_type} {event_data}")


async def _handle_server_command(message: dict[str, Any]) -> None:
    data = message.get("data", {})
    msg_id = message.get("msg_id")
    if not isinstance(data, dict) or not isinstance(msg_id, str):
        return

    command = data.get("command")
    args = data.get("args", [])
    if not isinstance(command, str):
        await _send_reply(msg_id, {"error_type": "invalid_message"}, status="error")
        return
    if not isinstance(args, list):
        args = []

    try:
        result = await dispatch_command(command, [str(arg) for arg in args], PermissionLevel.ADMIN)
    except Exception as error:
        await _send_reply(
            msg_id,
            {"error_type": "command_failed", "error_message": str(error)},
            status="error",
        )
        return

    await _send_reply(msg_id, {"command": command, "reply": result})


async def _handle_server_error(message: dict[str, Any]) -> None:
    data = message.get("data", {})
    if isinstance(data, dict):
        logger.error(
            "Mineflayer WebSocket error: %s %s",
            data.get("error_type", "unknown"),
            data.get("error_message", ""),
        )
    else:
        logger.error(f"Mineflayer WebSocket error: {message}")


async def _poll_players_loop() -> None:
    while True:
        await asyncio.sleep(max(config.mineflayer_ws_player_poll_interval, 1))
        if not _is_ws_connected() or not current_bot_id:
            continue
        try:
            reply = await _send_request("player", bot_id=current_bot_id)
            await _record_player_reply(reply)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f"轮询在线玩家失败: {error}")


async def _record_player_reply(reply: dict[str, Any]) -> None:
    from .player_tracker import record_snapshot

    result = reply.get("data", {}).get("result", {})
    if not isinstance(result, dict):
        return

    raw_players = result.get("player", [])
    if not isinstance(raw_players, list):
        return

    players: list[dict[str, str]] = []
    for raw_player in raw_players:
        if not isinstance(raw_player, dict):
            continue
        username = raw_player.get("username")
        if not isinstance(username, str) or not username:
            continue
        players.append(
            {
                "name": username,
                "uuid": str(raw_player.get("uuid") or ""),
                "skin_url": str(raw_player.get("skin_url") or ""),
            }
        )

    bot_username = result.get("bot_username")
    record_snapshot(
        players,
        datetime.now(timezone.utc).isoformat(),
        bot_username=str(bot_username or ""),
    )


async def _send_bridge_message(message: str) -> None:
    if not message:
        return

    delivered = await _try_send_bridge_message(message)
    if delivered:
        return

    pending_bridge_messages.append(message)
    logger.warning("桥接消息暂存：OneBot 尚未就绪或目标不可用，等待连接恢复后补发")


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
            await bot_instance.call_api("send_group_msg", group_id=target_id, message=message)
        else:
            await bot_instance.call_api("send_private_msg", user_id=target_id, message=message)
        return True
    except Exception as error:
        logger.error(f"发送桥接消息失败: {error}")
        return False


async def _flush_pending_bridge_messages() -> None:
    if not pending_bridge_messages:
        return

    pending = list(pending_bridge_messages)
    pending_bridge_messages.clear()
    sent_count = 0

    for index, pending_message in enumerate(pending):
        delivered = await _try_send_bridge_message(pending_message)
        if delivered:
            sent_count += 1
            continue

        for remaining_message in pending[index:]:
            pending_bridge_messages.append(remaining_message)
        break

    if sent_count > 0:
        logger.info(f"已补发桥接消息 {sent_count} 条")


async def dispatch_command(
    command: str,
    args: list[str],
    permission_level: str | PermissionLevel,
    player_name: str | None = None,
) -> str:
    if isinstance(permission_level, str):
        try:
            level = PermissionLevel(permission_level)
        except ValueError:
            level = PermissionLevel.USER
    else:
        level = permission_level

    full_command = command if not args else f"{command} {' '.join(args)}"
    if level < PermissionLevel.ADMIN and full_command not in {"mc status"}:
        return f"权限不足：{full_command}"

    if command == "mc":
        return await _dispatch_mc_command(args)
    if command in {"tpa", "home"}:
        return await _delegate_to_ws(command, args, level, player_name=player_name)
    if command == "git":
        return await _dispatch_git_command(args)
    return f"未知指令：{command}"


async def _dispatch_mc_command(args: list[str]) -> str:
    if not args:
        return "用法: mc <connect|disconnect|logout|status>"

    sub = args[0]
    if sub == "connect":
        account = _parse_positive_int(args[1]) if len(args) > 1 else None
        server = _parse_positive_int(args[2]) if len(args) > 2 else None
        _, message = await _connect_ws(account_preset=account, server_preset=server)
        return message

    if sub == "disconnect":
        _, message = await _close_ws_connection(persist_state=True)
        return message

    if sub == "logout":
        _, message = await _logout_current_bot()
        return message

    if sub == "status":
        return _format_status()

    return "用法: mc <connect|disconnect|logout|status>"


def _parse_positive_int(value: str) -> int | None:
    if not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _format_status() -> str:
    state = _load_runtime_state()
    ws_text = "已连接" if _is_ws_connected() else "未连接"
    auth_text = "已认证" if authenticated else "未认证"
    bot_text = current_bot_id or state.get("mc_bot_id") or "未绑定"
    bot_state = current_bot_state or state.get("mc_bot_state") or "unknown"
    target_text = _format_target(state)
    pending_text = str(len(pending_bridge_messages))
    poller_text = (
        "运行中"
        if player_poll_task is not None and not player_poll_task.done()
        else "未运行"
    )

    return (
        "MC WebSocket 状态：\n"
        f"- 连接: {ws_text}\n"
        f"- 认证: {auth_text}\n"
        f"- MC Bot: {bot_text}\n"
        f"- MC 状态: {bot_state}\n"
        f"- 推送目标: {target_text}\n"
        f"- 玩家轮询: {poller_text}\n"
        f"- 待补发消息: {pending_text}"
    )


async def _dispatch_git_command(args: list[str]) -> str:
    if not args:
        return "你说得对，但是git是一款由Linus Torvalds开发的......"
    if args[0] == "pull":
        return await _execute_git_pull()
    return "干什么?!"


async def _execute_git_pull() -> str:
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


async def _delegate_to_ws(
    command: str,
    args: list[str],
    level: PermissionLevel,
    *,
    player_name: str | None = None,
) -> str:
    if not _is_ws_connected():
        return "WebSocket 未连接，无法执行指令"
    if not current_bot_id:
        return "当前未绑定 MC bot，无法执行指令"

    permission = "admin" if level >= PermissionLevel.ADMIN else "user"
    extra: dict[str, Any] = {"permission": permission}
    if player_name:
        extra["player_name"] = player_name

    try:
        reply = await _send_request(
            "command",
            {"command": command, "args": args, "wait": True},
            bot_id=current_bot_id,
            extra=extra,
            timeout=max(config.mineflayer_ws_request_timeout, 15),
        )
    except asyncio.TimeoutError:
        return "指令执行超时"
    except Exception as error:
        return f"指令执行失败：{error}"

    result = reply.get("data", {}).get("result")
    return _message_result_text(result)


driver = get_driver()


@driver.on_startup
async def _restore_ws_on_startup() -> None:
    state = _load_runtime_state()
    if state.get("should_connect"):
        logger.info("检测到 WebSocket 自动恢复已开启，将在 OneBot 连接后恢复连接")


@driver.on_bot_connect
async def _restore_target_bot_on_connect(bot: Bot) -> None:
    global active_bot

    state = _load_runtime_state()
    saved_onebot_id = state.get("onebot_id")
    can_bind = (
        not isinstance(saved_onebot_id, str)
        or not saved_onebot_id
        or str(bot.self_id) == saved_onebot_id
    )
    if not can_bind:
        return

    active_bot = bot
    logger.info(f"已恢复消息推送 Bot: {bot.self_id}")
    await _flush_pending_bridge_messages()

    if state.get("should_connect") and not _is_ws_connected():
        account = state.get("account_preset")
        server = state.get("server_preset")
        started, message = await _connect_ws(
            bot=bot,
            account_preset=account if isinstance(account, int) else None,
            server_preset=server if isinstance(server, int) else None,
            persist_state=False,
        )
        if started:
            logger.info("已在 OneBot 连接后自动恢复 Mineflayer WebSocket")
        else:
            logger.warning(f"自动恢复 Mineflayer WebSocket 失败: {message}")


@driver.on_shutdown
async def _close_ws_on_shutdown() -> None:
    if not _is_ws_connected():
        return
    logger.info("Bot 正在关闭，断开 Mineflayer WebSocket")
    await _close_ws_connection(persist_state=False)


@mc.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    arg_list = args.extract_plain_text().strip().split()
    command = arg_list[0] if arg_list else ""

    if command == "connect":
        account = _parse_positive_int(arg_list[1]) if len(arg_list) > 1 else None
        server = _parse_positive_int(arg_list[2]) if len(arg_list) > 2 else None
        success, message = await _connect_ws(
            bot=bot,
            event=event,
            account_preset=account,
            server_preset=server,
            persist_state=True,
        )
        await mc.send(message)
        await set_status_emoji(
            bot,
            message_id,
            EMOJI_STATUS_SUCCESS if success or _is_ws_connected() else EMOJI_STATUS_FAILED,
        )
        return

    if command == "disconnect":
        stopped, message = await _close_ws_connection(persist_state=True)
        await mc.send(message)
        await set_status_emoji(
            bot,
            message_id,
            EMOJI_STATUS_SUCCESS if stopped else EMOJI_STATUS_FAILED,
        )
        return

    if command == "logout":
        success, message = await _logout_current_bot()
        await mc.send(message)
        await set_status_emoji(
            bot,
            message_id,
            EMOJI_STATUS_SUCCESS if success else EMOJI_STATUS_FAILED,
        )
        return

    if command == "status":
        await mc.send(_format_status())
        await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)
        return

    await mc.send("用法: mc <connect|disconnect|logout|status>")
    await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)


@tpa_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    arg_text = args.extract_plain_text().strip()
    arg_list = arg_text.split() if arg_text else []
    result = await dispatch_command("tpa", arg_list, PermissionLevel.ADMIN)
    if result:
        await tpa_cmd.send(result)

    success = "失败" not in result and "不足" not in result and "超时" not in result
    await set_status_emoji(
        bot,
        message_id,
        EMOJI_STATUS_SUCCESS if success else EMOJI_STATUS_FAILED,
    )


@home_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()) -> None:
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    arg_text = args.extract_plain_text().strip()
    arg_list = arg_text.split() if arg_text else []
    result = await dispatch_command("home", arg_list, PermissionLevel.ADMIN)
    if result:
        await home_cmd.send(result)

    success = "失败" not in result and "超时" not in result
    await set_status_emoji(
        bot,
        message_id,
        EMOJI_STATUS_SUCCESS if success else EMOJI_STATUS_FAILED,
    )


@bridge_input.handle()
async def _(bot: Bot, event: Event) -> None:
    if not _is_ws_connected() or not current_bot_id:
        return
    if not _event_matches_runtime_target(bot, event):
        return

    sender_id = getattr(event, "user_id", None)
    if sender_id is not None and str(sender_id) == str(bot.self_id):
        return

    plain_text = event.get_plaintext().strip() if hasattr(event, "get_plaintext") else ""
    if not plain_text:
        return
    if plain_text.startswith(("/mc", "/connect", "#mc", "#tpa", "#home")):
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
            bot_id=current_bot_id,
            timeout=config.mineflayer_ws_request_timeout,
        )
    except Exception as error:
        logger.warning(f"转发 QQ 消息到 MC 失败: {error}")
