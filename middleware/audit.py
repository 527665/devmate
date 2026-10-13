"""DevMate 的工具审计中间件。

使用 ``awrap_tool_call`` 包裹每一次异步工具调用，在真正执行前后
分别写入审计日志；中间件只观测，不更新状态，也不拦截执行结果。

追加 Prometheus 埋点（黑板在 obs/metrics.py，这里只往上写数）：
每次工具调用记两笔——次数按 ok/error 分栏（AGENT_TOOL_CALLS）、耗时（AGENT_TOOL_DURATION）。
失败先记 error 再 raise 原样抛出；耗时记在 finally，失败调用也有耗时。
"""

import time
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command

from infra.logging import get_logger
from obs.metrics import AGENT_TOOL_CALLS, AGENT_TOOL_DURATION


logger = get_logger()


class ToolAuditMiddleware(AgentMiddleware):
    """在每次工具调用前后打印审计日志，并把次数/耗时记进 Prometheus 黑板。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[
            [ToolCallRequest],
            Awaitable[ToolMessage | Command],
        ],
    ) -> ToolMessage | Command:
        """包裹一次工具调用，并记录开始、结束和耗时。

        Args:
            request: LangChain 工具节点传入的工具调用请求。
            handler: 下一步处理器；调用它才会真正执行工具。

        Returns:
            工具执行返回的 ``ToolMessage`` 或 ``Command``，原样放行。
        """

        # 当前版本中 request.tool_call 是 dict，包含 name、args、id。
        tool_name = request.tool_call["name"]

        start = time.perf_counter()
        logger.info("🔧 调用工具：{}", tool_name)

        status = "ok"
        try:
            return await handler(request)
        except Exception:
            status = "error"
            raise                                  # 失败也计数，再原样抛出
        finally:
            elapsed = time.perf_counter() - start
            AGENT_TOOL_CALLS.labels(tool_name, status).inc()        # 埋点：次数
            AGENT_TOOL_DURATION.labels(tool_name).observe(elapsed)  # 埋点：耗时
            logger.info("🔧 工具 {} {}（{:.0f} ms）", tool_name, status, elapsed * 1000)
