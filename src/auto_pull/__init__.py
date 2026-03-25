import asyncio
import os
import sys
from pathlib import Path


import nonebot
from nonebot import get_plugin_config, logger, on_command
from nonebot.adapters import Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me

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

@git.handle()
async def _(args: Message = CommandArg()):
    sub_command = args.extract_plain_text().strip()
    if sub_command == "pull":
        await git.send("pulling...")

        process = await asyncio.create_subprocess_shell(
            (
                'git -c '
                'url."https://gh-proxy.org/https://github.com/".insteadOf='
                '"https://github.com/" pull'
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # 等待命令执行完成并获取输出
        stdout, stderr = await process.communicate()

        output = stdout.decode().strip() if stdout else ""
        err_output = stderr.decode().strip() if stderr else ""

        if process.returncode == 0:
            # 成功拉取代码
            await git.send(f"更新成功:\n{output}\n正在重启进程...")

            # 执行重启操作
            restart_bot()
        else:
            # 拉取失败
            await git.send(f"更新失败 (错误码 {process.returncode}):\n{err_output}")
    elif not sub_command:
        await git.finish("你说得对，但是git是一款由Linus Torvalds开发的......")
    else:
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
