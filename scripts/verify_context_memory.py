"""验证 Skills / Memory 是否生效。

验证两件事：
① DevMate 是否命中并加载了规范（做接口任务时去读 fastapi-endpoint）；
② 是否记住了铁律（做定价任务时主动用 Decimal、附带测试）。

运行方式（项目根目录）：

    uv run python -m scripts.verify_context_memory
"""

import asyncio
import sys

from agent.main import build_agent
from scripts._common import print_all


def _prepare_stdout() -> None:
    """尽量使用 UTF-8，兼容 Windows 中文终端。"""

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


async def main() -> None:
    """发起一个同时命中 skills 与 memory 的任务，观察 Agent 是否遵守。"""

    agent = build_agent(user_id="alice", channel="cli")

    # 这个任务会命中 fastapi-endpoint 规范，且涉及金额 → 应触发 memory 的 Decimal 铁律。
    task = (
        "在 app/ 下新增一个订单接口：POST /orders/quote，"
        "接收一组商品（单价、数量），返回订单总价。"
        "请遵循团队的接口规范，并按团队约定处理金额。"
    )

    # FilesystemBackend 下必须带 thread_id（checkpointer 需要）。
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": task}]},
        config={"configurable": {"thread_id": "ch04-demo"}},
    )

    print_all(result)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    _prepare_stdout()
    asyncio.run(main())
