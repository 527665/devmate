"""第一次运行 DevMate（异步 ``ainvoke``）。

运行方式（项目根目录）：

    uv run python -m scripts.smoke_agent
"""

import asyncio
import sys

from agent.main import build_agent
from scripts._common import print_all


# 多步任务：提示 DevMate 先用 write_todos 列计划，方便观察任务清单。
MULTI_STEP_TASK = (
    "为订单服务实现一个折扣计算模块，放在 discount.py：\n"
    "1. calc_subtotal(items) 计算小计（items 形如 "
    "[{'price': 10.0, 'qty': 2}, ...]）；\n"
    "2. apply_coupon(subtotal, coupon) 应用优惠券，支持'满减'和'打折'两种类型；\n"
    "3. calc_final_total(items, coupon, vip_level) 综合计算最终应付，VIP 等级越高额外折扣越多；\n"
    "每个函数都要类型注解、docstring，并处理边界情况（空列表、无效优惠券）。"
)

# 简单任务（对照用）：模型大概率不列计划，任务清单可能为空。
SIMPLE_TASK = (
    "为订单服务写一个计算订单总价的函数 calc_order_total(items)，"
    "items 形如 [{'price': 10.0, 'qty': 2}, ...]，返回所有条目 price*qty 之和。"
    "放在 pricing.py 里，带类型注解和 docstring。"
)


async def main() -> None:
    """创建 DevMate，异步执行一次示例任务并打印结果。"""

    agent = build_agent()  # 创建阶段是同步工厂调用。

    # 想对照“简单任务不列计划”，可以改成 SIMPLE_TASK。
    task = MULTI_STEP_TASK

    # 调用阶段是异步的：必须使用 await agent.ainvoke(...)。
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": task}]}
    )

    print_all(result)


if __name__ == "__main__":
    # 与 verify_env.py 保持一致，兼容 Windows 异步运行环境。
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
