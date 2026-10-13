"""api/routes/issues.py —— 提交 Issue 的业务路由"""
from uuid import uuid4

from fastapi import APIRouter, Depends, Request   # Request 给 /secure 端点用（取 app.state）

from agent.main import build_agent
from api.schemas import IssueRequest, IssueResponse
from api.deps import get_checkpointer, get_store
from channels.base import InboundMessage
from channels.handler import handle_message
from gateway.auth import require_api_key

router = APIRouter(prefix="/issues", tags=["issues"])


@router.post("", response_model=IssueResponse)
async def submit_issue(
    req: IssueRequest,
    tenant_id: str = Depends(require_api_key),   # ← 会调模型就必须先过鉴权
    checkpointer=Depends(get_checkpointer),   # 注入 lifespan 建好的共享资源
    store=Depends(get_store),
) -> IssueResponse:
    # thread_id：客户端传了就用它（续聊/断点续跑），没传就新建
    thread_id = req.thread_id or uuid4().hex

    # 构建 agent（轻组装），传入共享的持久化资源
    agent = build_agent(
        checkpointer=checkpointer,
        store=store,
        user_id=req.user_id,
        channel=req.channel,
    )

    # 异步调用，带 thread_id —— 同一 thread_id 下次请求会带着历史上下文继续
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": req.issue}]},
        config={"configurable": {"thread_id": thread_id}},
    )

    reply = result["messages"][-1].content if result.get("messages") else ""
    return IssueResponse(thread_id=thread_id, reply=reply)


# 注意：本 router 的 prefix 是 "/issues"，所以下面这个端点的完整路径是
# /issues/secure，不是 /secure。
@router.post("/secure")
async def submit_issue_secure(
    req: IssueRequest,
    request: Request,
    tenant_id: str = Depends(require_api_key),   # ← 鉴权返回租户 id
):
    """需要 X-API-Key 的端点；租户身份贯穿到会话隔离。

    与上面 /issues 的区别：
    ① 多一道鉴权依赖，key 不对直接 401，不进业务（一次模型调用都不会发生）；
    ② 不自己起 uuid4 当 thread_id，而是走 handle_message → thread_id_for()，
       把 tenant 纳入派生因子；
    ③ 资源从 request.app.state 直取（与 channels/webhook.py 同一路子）；
       本文件上面那种 Depends(get_checkpointer) 也完全等价，两种写法都能拿到同一个对象。
    """
    inbound = InboundMessage(
        channel=req.channel, user_id=req.user_id,
        text=req.issue, conversation_id=req.thread_id or req.user_id,
    )
    reply = await handle_message(
        inbound,
        request.app.state.checkpointer,
        request.app.state.store,
        tenant_id=tenant_id,                     # ← 一路传到 thread 派生，按租户隔离
    )
    # 这里直接返 dict（FastAPI 会帮你转 JSON）：IssueResponse 没 tenant 字段、
    # 且 thread_id 是必填，而本端点的 thread_id 是内部派生出来的，不想对外暴露。
    return {"reply": reply, "tenant": tenant_id}
