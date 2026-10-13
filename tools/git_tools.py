"""DevMate 的 Git 工具（commit / diff / 提 PR）。

用 ``@tool`` 把对 git 的封装暴露成工具。docstring 写清楚用途，
模型据此决定何时调用。

注意：这些工具会在 DevMate 的工作目录里执行 git；
真正"安全地执行"要配合沙箱，
本地开发阶段先用 ``asyncio.create_subprocess_exec`` 演示。
"""

import asyncio
import sys

from langchain.tools import tool

from infra.logging import get_logger


def _prepare_windows_loop() -> None:
    """Windows 上 asyncio 子进程需要 ProactorEventLoop（默认即是），
    但项目脚本为兼容其他异步库设置了 SelectorEventLoop，会导致子进程不可用。
    这里在事件循环创建前恢复 Proactor（仅 Windows）。"""

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


logger = get_logger()

_prepare_windows_loop()


async def _run(cmd: list[str], cwd: str = ".") -> str:
    """异步跑一条命令，返回 stdout（出错则返回 stderr）。"""

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        return f"[git error] {err.decode().strip()}"
    return out.decode().strip() or "(ok)"


@tool
async def git_status() -> str:
    """查看当前 Git 仓库的状态（git status），返回有哪些改动、是否干净。"""

    return await _run(["git", "status", "--short"])


@tool
async def git_diff(path: str = "") -> str:
    """查看 Git 改动的具体内容（git diff）。可选 path 只看某个文件/目录的 diff。"""

    cmd = ["git", "diff"]
    if path:
        cmd.append(path)
    return await _run(cmd)


@tool
async def git_commit(message: str) -> str:
    """把当前已暂存的改动提交（git add -A && git commit）。message 是提交信息。

    用于在完成一处代码改动、并希望记录一个提交点时调用。
    """

    await _run(["git", "add", "-A"])
    return await _run(["git", "commit", "-m", message])


@tool
async def open_pull_request(title: str, body: str = "") -> str:
    """创建一个 Pull Request（需要已配置 gh CLI 并登录）。title 是 PR 标题，body 是描述。

    ⚠️ 这是敏感操作：会给它配人在回路审批，提 PR 前需人工确认。
    """

    cmd = ["gh", "pr", "create", "--title", title, "--body", body or title]
    return await _run(cmd)
