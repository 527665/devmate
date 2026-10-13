"""tools/test_tools.py —— 跑测试的封装工具

⚠️ 重要：本地开发阶段这里用 subprocess 跑 pytest 只是过渡。
真正"安全地让 DevMate 跑测试"要在沙箱里——
绝不能在生产宿主机上直接跑模型生成的命令。
"""

import asyncio
import subprocess
import sys

from langchain.tools import tool


@tool
async def run_pytest(path: str = "tests/") -> str:
    """运行 pytest 跑测试，返回测试结果摘要。path 指定测试目录/文件。

    用于在改完代码后验证是否通过测试。
    """

    # Windows 上裸写 "pytest" 可能找不到可执行文件，用当前解释器 -m pytest 更可靠。
    #
    # ⚠ 不用 asyncio.create_subprocess_exec：Windows 的 Selector 事件循环不支持
    #   子进程（3.13 实测抛 NotImplementedError），而 psycopg 异步又强制要求
    #   Selector 循环——两者在正式链路（api / worker / 飞书）里直接互斥。
    #   解法：把【同步】subprocess.run 丢进线程池，与事件循环类型彻底解耦，
    #   Selector / Proactor 下都能跑，且不阻塞事件循环。
    def _run_pytest() -> str:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", path, "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return proc.stdout.decode("utf-8", errors="replace")

    out = await asyncio.to_thread(_run_pytest)
    return out[-3000:]  # 截断，避免过长结果灌爆上下文
