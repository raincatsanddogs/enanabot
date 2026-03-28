import asyncio
import json
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import nonebot
from nonebot import get_bots, get_driver, get_plugin_config, logger, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="mineflayer-js-bridge",
    description="A bridge for connecting Mineflayer and JavaScript",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)

sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent.joinpath("plugins").resolve())
)

mc = on_command("mc", rule=to_me(), aliases={"connect"}, priority=5, permission=SUPERUSER)

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

@mc.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    start = args.extract_plain_text().strip()

    if start == "start":
        _, message = await _start_js_process(bot=bot, event=event, persist_state=True)
        await mc.finish(message)

    elif start == "stop":
        _, message = await _stop_js_process(persist_state=True)
        await mc.finish(message)

    elif start == "status":
        state = _load_runtime_state()
        running = js_process is not None and js_process.returncode is None
        running_text = "运行中" if running else "未运行"
        should_start_text = "开启" if state.get("should_start") else "关闭"
        bot_text = str(state.get("bot_id") or "未设置")
        target_text = _format_target(state)
        pending_text = str(len(PENDING_BRIDGE_MESSAGES))

        await mc.finish(
            "MC Bridge 状态:\n"
            f"- JS 进程: {running_text}\n"
            f"- 重启后自动恢复: {should_start_text}\n"
            f"- 推送 Bot: {bot_text}\n"
            f"- 推送目标: {target_text}\n"
            f"- 待补发消息: {pending_text}"
        )
    else:
        await mc.finish("干什么?!")

# ==========================================
# 2. 全局缓存 (核心思路)
# ==========================================
# 这个字典会常驻内存，后续所有的翻译请求都直接从这里读取，不再碰硬盘
_LANG_CACHE = {
    "zh_cn": {},
    "en_us": {}
}

# ==========================================
# 3. 初始化加载 (只在模块被首次 import 时运行一次)
# ==========================================
def _load_language_file(file_path):
    """内部函数：读取 JSON 文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"警告：无法加载语言文件 '{file_path}'。原因: {e}")
        return {}

# 任务 1：只负责监听标准输出 (正常日志和 JSON 数据)
async def read_stdout(process: asyncio.subprocess.Process):
    while process.returncode is None:
        line = await process.stdout.readline()
        if not line:
            break
        try:
            data = json.loads(line.decode())
            logger.info(f"收到来自 JS 的对象: {data}")
            received_msg = data["msg"]
            output = ""
            if translate:=received_msg.get("translate"):
                #if "multiplayer.player" in translate:
                    # status = "加入" if (received_msg["type"] == "join") else "离开"
                    # output = f"{received_msg['params'][0]['name']} {status}了游戏"
                #if "death" in translate:
                match len(received_msg["params"]):
                    case 1:
                        output = translate_mc_key(translate,"zh_cn",received_msg['params'][0]['name'])
                        #f"{received_msg['params'][0]['name']} 死了"
                    case 2:
                        output = translate_mc_key(translate,"zh_cn",received_msg['params'][0]['name'], 
                                                                    received_msg['params'][1]['name'])
                        #f"{received_msg['params'][0]['name']} 被 {received_msg['params'][1]['name']} 杀死了"
                    case 3:
                        output = translate_mc_key(translate,"zh_cn",received_msg['params'][0]['name'], 
                                                                    received_msg['params'][1]['name'], 
                                                                    received_msg['params'][2]['name'])
                        #f"{received_msg['params'][0]['name']} 被 {received_msg['params'][1]['name']} 用 {received_msg['params'][2]['name']} 杀死了"
            elif received_msg["type"] == "chat":
                output = f"{received_msg['text']}"
            else:
                continue
            # 统一走发送函数，支持 event 上下文和持久化目标两种转发方式。
            if output:
                await _send_bridge_message(f"[插件服]>>{output}")
        except json.JSONDecodeError:
            logger.debug(f"JS 普通输出: {line.decode().strip()}")
        except Exception as e:
            logger.error(f"解析 JS 数据失败: {e}")

# 任务 2：只负责监听错误输出
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
    logger.info("✅ MC 语言包已成功加载到内存！") # 你可以在控制台看到这句话只打印了一次

    await asyncio.gather(
        read_stdout(process),
        read_stderr(process),
        return_exceptions=True # 加上这个参数，防止其中一个循环崩溃导致另一个也被强制关掉
    )

    await process.wait()
    return_code = process.returncode

    # 仅在非手动停止且退出码非 0 时，主动推送告警消息。
    if (not js_stop_requested) and return_code not in (None, 0):
        err_summary = "\n".join(js_stderr_lines).strip()
        if not err_summary:
            err_summary = "(无 stderr 输出)"
        alert = f"[插件服] JS 进程异常退出，退出码: {return_code}\n最近错误输出:\n{err_summary}"

        await _send_bridge_message(alert)

    js_stop_requested = False
    if js_process is process:
        js_process = None
# 将键名翻译为文本，generated by 哈gemi
def load_language_file(file_path):
    """
    加载本地的 Minecraft .json 语言文件。
    
    参数:
        file_path (str): 本地 json 文件的路径
    返回:
        dict: 包含键值对的语言字典
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误：找不到文件 '{file_path}'。请确保文件路径正确。")
        return {}
    except json.JSONDecodeError:
        print(f"错误：'{file_path}' 不是有效的 JSON 格式。")
        return {}

def translate_mc_key(key, lang="zh_cn", *args):
    """
    根据语言字典翻译键名，并替换其中的占位符。

    参数:
        lang_data (dict): 由 load_language_file 返回的语言字典
        key (str): 需要翻译的键名 (例如 "death.attack.fall")
        *args: 用于替换占位符的动态参数
    返回:
        str: 翻译并替换好变量的最终文本
    """

    # 1. 指定语言字典 (直接从内存缓存中拿)
    lang_data = _LANG_CACHE.get(lang, _LANG_CACHE["zh_cn"])

    # 1. 从字典中获取原始翻译文本
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

    # 2. 将 MC 的占位符 (%s, %d, %1$s, %2$s 等) 替换为 Python 的 {}
    # [sd] 兼容了字符串(%s)和数字(%d)的占位符格式
    formatted_text = re.sub(r'%(\d+\$)?[sd]', ' {} ', raw_text)

    # 3. 填入变量
    try:
        return formatted_text.format(*processed_args)
    except IndexError:
        # 如果传入的 args 数量少于占位符数量，为了防止报错，原样返回带有占位符的文本
        return raw_text