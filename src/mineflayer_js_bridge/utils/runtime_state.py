from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Event

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
LEGACY_CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs"
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
    "enable_push": True,
}


def load_runtime_state() -> dict[str, bool | str | int | None]:
    state: dict[str, bool | str | int | None] = dict(RUNTIME_STATE_DEFAULT)
    path = (
        RUNTIME_STATE_PATH
        if RUNTIME_STATE_PATH.exists()
        else LEGACY_RUNTIME_STATE_PATH
    )
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

    state["enable_push"] = bool(loaded.get("enable_push", True))

    if path == LEGACY_RUNTIME_STATE_PATH:
        save_runtime_state(**state)

    return state


def save_runtime_state(
    *,
    should_connect: bool,
    mc_bot_id: str | None = None,
    mc_bot_state: str | None = None,
    onebot_id: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    account_preset: int | None = None,
    server_preset: int | None = None,
    enable_push: bool | None = None,
    clear_current_bot: bool = False,
) -> None:
    previous = (
        load_runtime_state()
        if RUNTIME_STATE_PATH.exists()
        else dict(RUNTIME_STATE_DEFAULT)
    )
    payload: dict[str, bool | str | int | None] = {
        "should_connect": should_connect,
        "mc_bot_id": None
        if clear_current_bot
        else mc_bot_id
        if mc_bot_id is not None
        else previous.get("mc_bot_id"),
        "mc_bot_state": (
            mc_bot_state if mc_bot_state is not None else previous.get("mc_bot_state")
        ),
        "onebot_id": onebot_id if onebot_id is not None else previous.get("onebot_id"),
        "target_type": (
            target_type if target_type is not None else previous.get("target_type")
        ),
        "target_id": target_id if target_id is not None else previous.get("target_id"),
        "account_preset": account_preset
        if account_preset is not None
        else previous.get("account_preset"),
        "server_preset": server_preset
        if server_preset is not None
        else previous.get("server_preset"),
        "enable_push": enable_push
        if enable_push is not None
        else previous.get("enable_push", True),
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


def extract_target_from_event(bot: Bot, event: Event) -> dict[str, str | int] | None:
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


def format_target(state: dict[str, bool | str | int | None]) -> str:
    target_type = state.get("target_type")
    target_id = state.get("target_id")
    if target_type == "group" and isinstance(target_id, int):
        return f"群聊 {target_id}"
    if target_type == "private" and isinstance(target_id, int):
        return f"私聊 {target_id}"
    return "未设置"


def runtime_event_matches_target(event: Event, target: dict[str, Any]) -> bool:
    target_type = target.get("target_type")
    target_id = target.get("target_id")
    if not isinstance(target_id, int):
        return False

    group_id = getattr(event, "group_id", None)
    user_id = getattr(event, "user_id", None)
    if target_type == "group":
        return isinstance(group_id, int) and group_id == target_id
    if target_type == "private":
        return (
            not isinstance(group_id, int)
            and isinstance(user_id, int)
            and user_id == target_id
        )
    return False
