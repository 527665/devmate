"""channels/handler.py —— 渠道无关的消息处理核心

所有渠道把消息归一化成 InboundMessage 后，都调 handle_message。
这里不关心消息来自哪——这正是"渠道差异收敛在适配器、核心只认统一请求"的体现。
"""
from channels.base import InboundMessage
from channels.session import thread_id_for
from agent.main import build_agent
from infra.logging import get_logger

logger = get_logger()


async def handle_message(
    msg: InboundMessage, checkpointer, store, tenant_id: str = "default"
) -> str:
    """处理一条归一化消息，返回 DevMate 的回复文本。

    checkpointer/store 由调用方（服务的 app.state）传入复用。
    tenant_id 由鉴权层一路传下来，参与 thread_id 派生（多租户会话隔离）。
    """
    thread_id = thread_id_for(msg, tenant_id)  # (租户,渠道,会话) → 稳定 thread_id
    logger.info(
        "处理消息：tenant={} channel={} user={} thread={}",
        tenant_id, msg.channel, msg.user_id, thread_id,
    )

    agent = build_agent(
        checkpointer=checkpointer,
        store=store,
        user_id=msg.user_id,
        channel=msg.channel,
        # 注：tenant 目前只作 thread 隔离，还没贯通到中间件——
        # RequestContextMiddleware 只有 user_id/channel，无 tenant 字段（中间件待增强）。
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": msg.text}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["messages"][-1].content if result.get("messages") else ""
