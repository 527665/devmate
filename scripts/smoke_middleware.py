"""带审计/成本/上下文中间件运行 DevMate。

运行方式（项目根目录）：

    uv run python -m scripts.smoke_middleware
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
    """传入身份，观察三条中间件的注入、审计与计量效果。"""

    # 传入身份，观察 RequestContextMiddleware 注入效果。
    agent = build_agent(user_id="alice", channel="cli")

    task = (
        "为订单服务实现一个折扣计算模块，放在 discount.py："
        "calc_subtotal、apply_coupon、calc_final_total 三个函数，"
        "带类型注解、docstring，处理空列表与无效优惠券。"
    )

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": task}]}
    )

    print_all(result)

    # 顺带查看成本中间件累加进状态的 token（CostState 的字段）。
    print("\n=== 累计 token（来自 CostMeterMiddleware）===")
    print("input :", result.get("total_input_tokens"))
    print("output:", result.get("total_output_tokens"))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
