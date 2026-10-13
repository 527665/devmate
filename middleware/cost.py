"""DevMate 的成本计量中间件。

使用 ``awrap_model_call`` 包裹每次模型调用，调用完成后从返回消息的
``usage_metadata`` 读取 token 用量，并通过 ``ExtendedModelResponse`` +
``Command`` 把累计用量写进 Agent 状态。本模块只计量；超预算熔断在
后续模块基于它实现。

追加 Prometheus 埋点（黑板在 obs/metrics.py，这里只往上写数）：
每次模型调用记三笔——成功/失败计数（AGENT_LLM_CALLS）、耗时（AGENT_LLM_DURATION）、
token 换算成钱（record_llm_cost）。新增 __init__(tier) 给指标打档位标签。
"""

from collections.abc import Awaitable, Callable
import time

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import BaseMessage
from langgraph.types import Command
from typing_extensions import NotRequired

from infra.logging import get_logger
from obs.metrics import record_llm_cost, AGENT_LLM_DURATION, AGENT_LLM_CALLS


logger = get_logger()


class CostState(AgentState):
    """在 Agent 状态上扩展三个累计字段。

    ``NotRequired`` 表示调用方无需初始化这些键，中间件读取时用默认 0。
    """

    total_input_tokens: NotRequired[int]
    total_output_tokens: NotRequired[int]


class CostMeterMiddleware(AgentMiddleware):
    """统计每次模型调用的 token 用量，并累加进 Agent 状态。"""

    # 声明扩展状态 schema，框架据此知道状态里可以有这些字段。
    state_schema = CostState

    def __init__(self, tier: str = "strong") -> None:    # ← 新增 tier，用于给指标打标签
        super().__init__()
        self.tier = tier

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[
            [ModelRequest],
            Awaitable[ModelResponse],
        ],
    ) -> ExtendedModelResponse:
        """包裹一次模型调用，计量本次与累计 token 用量。

        Args:
            request: 模型节点传入的调用请求，含当前状态。
            handler: 下一步处理器；调用它才会真正请求大模型。

        Returns:
            包含原模型响应和状态更新指令的 ``ExtendedModelResponse``。
        """

        start = time.perf_counter()
        try:
            response = await handler(request)
            AGENT_LLM_CALLS.labels(self.tier, "ok").inc()        # 埋点：调用成功
        except Exception:
            AGENT_LLM_CALLS.labels(self.tier, "error").inc()     # 埋点：调用失败
            raise                                                 # 失败也计数，再抛给 fallback 处理
        finally:
            AGENT_LLM_DURATION.labels(self.tier).observe(time.perf_counter() - start)  # 埋点：耗时

        in_tok, out_tok = self._extract_usage(response)
        record_llm_cost(self.tier, in_tok, out_tok)              # 埋点：token + 成本

        # request.state 可能没有这两个键（NotRequired），用默认 0。
        prev_in = request.state.get("total_input_tokens", 0)
        prev_out = request.state.get("total_output_tokens", 0)
        new_in = prev_in + in_tok
        new_out = prev_out + out_tok

        logger.info(
            "💰 模型调用[{}]：in={} out={} | 累计 in={} out={}",
            self.tier,
            in_tok,
            out_tok,
            new_in,
            new_out,
        )

        # 官方规范：包裹式钩子更新状态时，返回 ExtendedModelResponse + Command。
        return ExtendedModelResponse(
            model_response=response,
            command=Command(
                update={
                    "total_input_tokens": new_in,
                    "total_output_tokens": new_out,
                }
            ),
        )

    @staticmethod
    def _extract_usage(response: ModelResponse) -> tuple[int, int]:
        """从模型响应中提取最后一条带用量信息的 AIMessage。

        ``usage_metadata`` 是 LangChain 统一的用量字段，包含
        ``input_tokens`` / ``output_tokens`` 等键。

        Args:
            response: 模型节点返回的响应对象。

        Returns:
            ``(input_tokens, output_tokens)``；找不到时返回 ``(0, 0)``。
        """

        messages: list[BaseMessage] = (
            getattr(response, "result", None)
            or getattr(response, "messages", None)
            or []
        )
        for message in reversed(messages):
            usage = getattr(message, "usage_metadata", None)
            if usage:
                return (
                    int(usage.get("input_tokens", 0)),
                    int(usage.get("output_tokens", 0)),
                )
        return 0, 0
