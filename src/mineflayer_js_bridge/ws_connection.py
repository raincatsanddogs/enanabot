"""WebSocket 连接生命周期和 NoneBot driver 钩子。"""

from __future__ import annotations

import asyncio
import contextlib

import websockets
from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot, Event

from . import ws_state
from .context import config
from .utils import extract_target_from_event, load_runtime_state, save_runtime_state
from .ws_bridge import _flush_pending_bridge_messages
from .ws_transport import _read_ws_messages, _send_request


def _ws_url() -> str:
    return f"ws://{config.mineflayer_ws_host}:{config.mineflayer_ws_port}"


def _is_ws_connected() -> bool:
    return ws_state.ws_connection is not None


async def _authenticate() -> None:
    reply = await _send_request("auth", {"token": config.mineflayer_ws_token})
    result = reply.get("data", {}).get("result")
    ws_state.authenticated = (
        bool(result.get("authenticated")) if isinstance(result, dict) else True
    )


async def _select_or_login_bot(account_preset: int, server_preset: int) -> str:
    state = load_runtime_state()
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
    ws_state.current_bot_id = bot_id
    if state:
        ws_state.current_bot_state = state


async def _connect_ws(
    *,
    bot: Bot | None = None,
    event: Event | None = None,
    account_preset: int | None = None,
    server_preset: int | None = None,
    persist_state: bool = True,
) -> tuple[bool, str]:
    async with ws_state.connection_lock:
        if bot and event:
            ws_state.active_bot = bot
            ws_state.active_event = event

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
            ws_state.ws_connection = await websockets.connect(
                _ws_url(),
                open_timeout=config.mineflayer_ws_request_timeout,
                close_timeout=5,
            )
            ws_state.ws_reader_task = asyncio.create_task(
                _read_ws_messages(),
                name="mineflayer-ws-reader",
            )
            await _authenticate()
            bot_id = await _select_or_login_bot(account, server)
        except Exception as error:
            await _close_ws_connection(persist_state=False)
            logger.error(f"连接 Mineflayer WebSocket 失败: {error}")
            return False, f"连接失败：{error}"

        if (
            ws_state.player_poll_task is None
            or ws_state.player_poll_task.done()
        ):
            from .ws_processor import _poll_players_loop

            ws_state.player_poll_task = asyncio.create_task(
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
    target = extract_target_from_event(bot, event) if bot and event else {}
    save_runtime_state(
        should_connect=should_connect,
        mc_bot_id=ws_state.current_bot_id,
        mc_bot_state=ws_state.current_bot_state,
        onebot_id=str(target.get("onebot_id")) if target else None,
        target_type=str(target.get("target_type")) if target else None,
        target_id=(
            int(target["target_id"]) if target and target.get("target_id") else None
        ),
        account_preset=account_preset,
        server_preset=server_preset,
    )


async def _close_ws_connection(*, persist_state: bool = True) -> tuple[bool, str]:
    current_task = asyncio.current_task()
    connection = ws_state.ws_connection
    was_connected = connection is not None
    ws_state.ws_connection = None
    ws_state.authenticated = False

    if ws_state.player_poll_task is not None:
        ws_state.player_poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ws_state.player_poll_task
        ws_state.player_poll_task = None

    if (
        ws_state.ws_reader_task is not None
        and ws_state.ws_reader_task is not current_task
    ):
        ws_state.ws_reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ws_state.ws_reader_task
        ws_state.ws_reader_task = None
    elif ws_state.ws_reader_task is current_task:
        ws_state.ws_reader_task = None

    if connection is not None:
        with contextlib.suppress(Exception):
            await connection.close()

    for future in ws_state.pending_replies.values():
        if not future.done():
            future.set_exception(RuntimeError("WebSocket 连接已关闭"))
    ws_state.pending_replies.clear()

    if persist_state:
        save_runtime_state(
            should_connect=False,
            mc_bot_id=ws_state.current_bot_id,
            mc_bot_state=ws_state.current_bot_state,
        )

    return was_connected, "已断开 WebSocket 连接" if was_connected else "WebSocket 未连接"


async def _logout_current_bot() -> tuple[bool, str]:
    if not _is_ws_connected():
        return False, "WebSocket 未连接"
    if not ws_state.current_bot_id:
        return False, "当前未绑定 MC bot"

    try:
        await _send_request("logout", bot_id=ws_state.current_bot_id)
    except Exception as error:
        return False, f"logout 失败：{error}"

    old_bot_id = ws_state.current_bot_id
    ws_state.current_bot_id = None
    ws_state.current_bot_state = "stopped"
    save_runtime_state(
        should_connect=False,
        mc_bot_state=ws_state.current_bot_state,
        clear_current_bot=True,
    )
    return True, f"已退出 MC bot: {old_bot_id}"


driver = get_driver()


@driver.on_startup
async def _restore_ws_on_startup() -> None:
    state = load_runtime_state()
    if state.get("should_connect"):
        logger.info("检测到 WebSocket 自动恢复已开启，将在 OneBot 连接后恢复连接")


@driver.on_bot_connect
async def _restore_target_bot_on_connect(bot: Bot) -> None:
    state = load_runtime_state()
    saved_onebot_id = state.get("onebot_id")
    can_bind = (
        not isinstance(saved_onebot_id, str)
        or not saved_onebot_id
        or str(bot.self_id) == saved_onebot_id
    )
    if not can_bind:
        return

    ws_state.active_bot = bot
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
