"""观察 DevMate 一步步工作（异步流式 ``astream``）。

运行方式（项目根目录）：

    uv run python -m scripts.smoke_agent_stream
"""

import asyncio
import sys
from typing import Any

from agent.main import build_agent


# 使用多步任务，便于观察模型的分析和工具调用过程。
MULTI_STEP_TASK = (
    "为订单服务实现一个折扣计算模块，放在 discount.py："
    "calc_subtotal、apply_coupon、calc_final_total 三个函数，"
    "带类型注解、docstring，并处理空列表与无效优惠券。"
)


def _prepare_stdout() -> None:
    """尽量使用 UTF-8，避免 Windows 终端无法输出中文或图标。"""

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _print_message(message: Any) -> None:
    """兼容 LangChain 消息对象和普通字典消息。"""

    pretty_print = getattr(message, "pretty_print", None)
    if callable(pretty_print):
        pretty_print()
        return

    if isinstance(message, dict):
        print(message.get("content", message))
        return

    print(getattr(message, "content", message))


async def main() -> None:
    """创建 DevMate，并实时打印 Agent 每一步产生的状态更新。"""

    _prepare_stdout()
    agent = build_agent()

    # stream_mode="updates" 只返回每一步新产生的状态更新，
    # 适合观察 Agent 的节点和工具调用过程。
    async for chunk in agent.astream(
        {"messages": [{"role": "user", "content": MULTI_STEP_TASK}]},
        stream_mode="updates",
    ):
        if not isinstance(chunk, dict):
            print(f"\n▶ 流式更新：{chunk}")
            continue

        for node_name, update in chunk.items():
            print(f"\n▶ 节点：{node_name}")
            messages = update.get("messages") if isinstance(update, dict) else None
            if messages:
                _print_message(messages[-1])


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
