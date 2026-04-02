import asyncio
import os
import sys
from pathlib import Path


import nonebot
from nonebot import get_plugin_config, logger, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
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
except ModuleNotFoundError:
    from utils.command_reaction import (
        EMOJI_STATUS_FAILED,
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="auto-pull",
    description="Automatically pull changes from a Git repository",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)

sub_plugins = nonebot.load_plugins(
    str(Path(__file__).parent.joinpath("plugins").resolve())
)

git = on_command("git", rule=to_me(), aliases={"git"}, priority=5, permission=SUPERUSER)


async def _stop_bridge_process_before_restart() -> None:

    bridge_module = (
        sys.modules.get("mineflayer_js_bridge")
        or sys.modules.get("src.mineflayer_js_bridge")
    )
    if bridge_module is None:
        for module_name, module in sys.modules.items():
            if module_name.endswith("mineflayer_js_bridge"):
                bridge_module = module
                break

    if bridge_module is None:
        return

    stop_func = getattr(bridge_module, "_stop_js_process", None)
    process = getattr(bridge_module, "js_process", None)
    if not callable(stop_func) or process is None:
        return

    if getattr(process, "returncode", None) is not None:
        return

    try:
        stopped, message = await stop_func(persist_state=False)
        if stopped:
            logger.info("重启前已停止 mineflayer_js_bridge 的 JS 子进程")
        else:
            logger.warning(f"重启前停止 JS 子进程返回: {message}")
    except Exception as error:
        logger.exception(f"重启前停止 JS 子进程失败: {error}")

@git.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    sub_command = args.extract_plain_text().strip()
    if sub_command == "pull":
        await git.send("pulling...")

        # 复用 mineflayer_js_bridge 中的 _execute_git_pull
        bridge_module = None
        for module_name, module in sys.modules.items():
            if module_name.endswith("mineflayer_js_bridge"):
                bridge_module = module
                break

        execute_fn = getattr(bridge_module, "_execute_git_pull", None) if bridge_module else None

        if callable(execute_fn):
            result = await execute_fn()
            await git.send(result)
            if "更新失败" in result:
                await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
                return
        else:
            # 回退：直接执行（兼容 bridge 未加载的情况）
            process = await asyncio.create_subprocess_shell(
                (
                    'git -c '
                    'url."https://gh-proxy.org/https://github.com/".insteadOf='
                    '"https://github.com/" pull'
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()
            output = stdout.decode().strip() if stdout else ""
            err_output = stderr.decode().strip() if stderr else ""

            if process.returncode == 0:
                await git.send(f"{output}")
                git_log_process = await asyncio.create_subprocess_shell(
                    'git log ORIG_HEAD..HEAD --pretty=format:"%h - %an : %s (%cr)"',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                log_stdout, _ = await git_log_process.communicate()
                await git.send(f"{log_stdout.decode().strip()}")
            else:
                await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
                await git.send(f"更新失败 (错误码 {process.returncode}):\n{err_output}")
                return

        await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)
        await git.send("正在重启······")
        await _stop_bridge_process_before_restart()
        # 执行重启操作
        restart_bot()
    elif not sub_command:
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        await git.finish("你说得对，但是git是一款由Linus Torvalds开发的......")
    else:
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        await git.finish("干什么?!")


def restart_bot() -> None:
    """
    重启机器人进程的方法
    """

    python = sys.executable
    # 优先使用解释器原始参数，避免丢失 `-m`/`-c` 等启动上下文。
    argv = list(getattr(sys, "orig_argv", []))
    if argv:
        argv[0] = python
    else:
        argv = [python, *sys.argv]

    # 部分运行环境下可能出现孤立 `-c`，会导致 Python 直接报错退出。
    if "-c" in argv:
        c_index = argv.index("-c")
        if c_index == len(argv) - 1:
            logger.warning(
                "检测到不完整的 -c 启动参数，回退到 `python -m nonebot` 重启"
            )
            argv = [python, "-m", "nonebot"]

    os.execv(python, argv)
