"""Git operation helpers shared by bot plugins."""

from __future__ import annotations

import asyncio


async def execute_git_pull() -> str:
    """Run git pull and return a human-readable result."""
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
