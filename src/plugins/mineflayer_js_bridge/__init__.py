from pathlib import Path

import nonebot
from nonebot import get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters import Message
from nonebot import on_command
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.permission import SUPERUSER

import json
import re
import asyncio
import os

from nonebot import logger

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

js_process = None
@mc.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    global js_process, active_bot, active_event
    start = args.extract_plain_text().strip()

    if start == "start":

        if js_process and js_process.returncode is None:
            await mc.finish("JS 脚本已经在运行中")

        active_bot = bot
        active_event = event
        # 启动子进程，重定向输入输出
        js_path = Path(__file__).parent / "src/index.js"

        js_process = await asyncio.create_subprocess_exec(
            "node", str(js_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # 开启一个后台任务持续监听 JS 的推送
        asyncio.create_task(listen_to_js())
        await mc.finish("开启链接中...")

    elif start == "stop":
        if js_process:
            js_process.terminate() # 发送 SIGTERM 信号
            await js_process.wait()
            js_process = None
            await mc.finish("已停止连接")
        else:
            await mc.finish("JS 脚本没有在运行")
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
async def read_stdout(js_process, active_bot, active_event):
    while js_process and js_process.returncode is None:
        line = await js_process.stdout.readline()
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
            # 这里可以根据 data 里的 user_id 主动推送到机器人终端
            if active_bot and active_event and output != "":
                await active_bot.send(active_event, f"[插件服]>>{output}") 
        except json.JSONDecodeError:
            logger.debug(f"JS 普通输出: {line.decode().strip()}")
        except Exception as e:
            logger.error(f"解析 JS 数据失败: {e}")

# 任务 2：只负责监听错误输出
async def read_stderr(js_process):
    while js_process and js_process.returncode is None:
        err = await js_process.stderr.readline()
        if not err:
            break
        logger.error(f"JS 错误输出: {err.decode().strip()}")

async def listen_to_js():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 拼接出 zh_cn.json 和 en_us.json 的绝对路径
    zh_cn_path = os.path.join(current_dir, "../../../configs/zh_cn.json")
    en_us_path = os.path.join(current_dir, "../../../configs/en_us.json")
    # 模块加载时，立即把数据读进内存
    _LANG_CACHE["zh_cn"] = _load_language_file(zh_cn_path)
    _LANG_CACHE["en_us"] = _load_language_file(en_us_path)
    logger.info("✅ MC 语言包已成功加载到内存！") # 你可以在控制台看到这句话只打印了一次

    await asyncio.gather(
        read_stdout(js_process, active_bot, active_event),
        read_stderr(js_process),
        return_exceptions=True # 加上这个参数，防止其中一个循环崩溃导致另一个也被强制关掉
    )
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