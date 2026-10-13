"""请求上下文注入中间件。

使用节点式钩子 ``abefore_agent``，在每次 Agent 运行开始时注入
``request_id`` / ``user_id`` / ``channel``，让日志能区分
"哪条请求、哪个用户、从哪个渠道来"。
"""

import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langgraph.runtime import Runtime
from typing_extensions import NotRequired

from infra.logging import get_logger


logger = get_logger()


class RequestContextState(AgentState):
    """扩展状态：把请求身份带进 Agent。

    ``NotRequired`` 表示调用方可以不传这些键，由中间件在运行开始时补全。
    """

    request_id: NotRequired[str]
    user_id: NotRequired[str]
    channel: NotRequired[str]


class RequestContextMiddleware(AgentMiddleware):
    """在每次 Agent 运行开始时注入/补全请求上下文。"""

    # 报备扩展状态，框架才允许状态里出现这几个字段。
    state_schema = RequestContextState

    def __init__(
        self,
        user_id: str = "anonymous",
        channel: str = "cli",
    ) -> None:
        """初始化默认身份信息。

        Args:
            user_id: 本次部署/调用方传入的用户标识。
            channel: 请求来源渠道，如 cli / api / web。
        """

        super().__init__()
        self.user_id = user_id
        self.channel = channel

    async def abefore_agent(
        self,
        state: RequestContextState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """生成请求 ID，并把身份三件套合并进 Agent 状态。

        Args:
            state: 当前 Agent 状态。
            runtime: LangGraph 运行时上下文（本钩子暂不使用）。

        Returns:
            节点式钩子规范：直接返回 dict，由框架按 reducer 合并进状态；
            本钩子总是注入，因此不返回 None。
        """

        request_id = uuid.uuid4().hex[:12]
        logger.info(
            "▶ 开始运行：request_id={} user={} channel={}",
            request_id,
            self.user_id,
            self.channel,
        )
        return {
            "request_id": request_id,
            "user_id": self.user_id,
            "channel": self.channel,
        }
