"""channels/webhook.py —— 通用 Webhook 接入（含飞书 challenge 验证）

两个工程要点：
① 回调验证：飞书 Webhook 模式首次会发 {"type":"url_verification","challenge":"..."}，
   必须原样返回 challenge（官方要求 1 秒内）；
② 快速返回 + 幂等：外部平台会重试，需立即 200 并对重复事件去重（用事件 id）。
"""
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse

from channels.base import InboundMessage
from channels.handler import handle_message
from gateway.auth import (
    feishu_decrypt_body,
    require_api_key,
    verify_feishu_signature,
    verify_feishu_token,
)
from infra.idempotency import seen_before
from infra.logging import get_logger
from obs.metrics import IDEMPOTENCY_HITS        # 幂等命中计数

logger = get_logger()

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/feishu")
async def feishu_webhook(request: Request, background: BackgroundTasks):
    """飞书 Webhook 回调模式入口（与长连接二选一）。

    飞书不能带我们的 X-API-Key，所以这道门换成飞书自己的三道校验：
    解密 → 比 token → 验签，任何一步不过直接 401，一次模型调用都不会发生
    （challenge 握手除外，见下面第 ③ 步）。

    顺序照抄 lark SDK 的 EventDispatcherHandler.do()：
    ① 先解密（配了 encrypt_key 时 body 是 {"encrypt": "..."}）；
    ② 再比 payload 里的 verification_token；
    ③ 若是 url_verification（配置回调地址时的握手）直接回 challenge —— SDK 在这
       一步之前就返回、不验签，所以我们也照做，否则握手可能失败；
    ④ 真正的业务事件才验签：签名算在【原始（密文）body】上，所以必须在解密前后
       都用同一份 raw，不能拿明文去算。
    没配 encrypt_key 时验签/解密都跳过并告警一次（见 gateway/auth.py），
    此时不要把本端点暴露到公网；长连接模式（feishu_ws.py）根本不走这里。
    """
    raw = await request.body()
    plain = feishu_decrypt_body(request, raw)
    verify_feishu_token(plain)
    body = json.loads(plain or b"{}")

    # ① challenge 验证：配置回调地址时飞书会先发这个，原样返回 challenge
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body.get("challenge", "")})

    # ② 业务事件：对原始 body 验签（放在任何花钱的动作之前）
    verify_feishu_signature(request, raw)

    # ③ 幂等去重：用 Redis（跨副本共享、带 TTL、重启不丢、原子无竞态）
    header = body.get("header", {})
    event_id = header.get("event_id")
    if event_id and await seen_before(request.app.state.redis, event_id, ttl_seconds=3600):
        IDEMPOTENCY_HITS.inc()   # 埋点：重复事件被挡（黑板在 obs/metrics.py）
        return JSONResponse({"code": 0})   # 重复事件，直接确认

    # ④ 解析消息事件 → 归一化（仅处理 im.message.receive_v1 文本）
    if header.get("event_type") == "im.message.receive_v1":
        message = body.get("event", {}).get("message", {})
        text = ""
        try:
            text = json.loads(message.get("content", "{}")).get("text", "").strip()
        except Exception:
            pass
        chat_id = message.get("chat_id", "")
        open_id = body.get("event", {}).get("sender", {}).get("sender_id", {}).get("open_id", "unknown")

        if text:
            inbound = InboundMessage(
                channel="feishu", user_id=open_id, text=text,
                conversation_id=chat_id, raw=body,
            )
            cp = request.app.state.checkpointer
            st = request.app.state.store

            async def _process():
                from channels.feishu import FeishuChannel
                fs = FeishuChannel()
                reply = await handle_message(inbound, cp, st)
                await fs.send(chat_id, reply)

            # 用 FastAPI 原生 BackgroundTasks（响应返回后执行）——比游离的
            # asyncio.create_task 正规：它由框架管理、保证在响应后调度。
            # ⚠️ 但它仍无持久化/重试/重启不丢——慢任务的可靠处理见 arq 队列（tasks/worker.py）。
            background.add_task(_process)

    # ⑤ 立即 200（飞书 3 秒约束）——不等 agent 跑完
    return JSONResponse({"code": 0})


@router.post("/generic")
async def generic_webhook(
    request: Request,
    tenant_id: str = Depends(require_api_key),   # ← 本端点同步跑完 agent，必须锁
):
    """通用 Webhook：任意外部系统 POST {"text": "...", "source": "..."} 触发 DevMate。

    ⚠️ 需要 X-API-Key：本端点是"同步等 agent 跑完才返回"，一次调用就是真金白银，
       裸奔等于把钱包挂在公网门上。CLI / Web 前端都要带 key（见 channels/cli.py、
       web/index.html）。

    ⚠️ 注意：本端点是【同步等待】agent 跑完才返回的——适合"任务较快"或调用方
    能接受长等待的场景（如脚本/CI）。一个跑几分钟的 Issue 会让调用方一直阻塞、
    甚至超时。让它"提交即返回、后台异步跑、结果可查"是任务队列的事。
    Web 前端目前调的就是这个端点，所以慢任务时网页会一直转圈——同样待任务队列改善。
    """
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"code": 1, "msg": "empty text"})

    inbound = InboundMessage(
        channel="webhook", user_id=body.get("source", "external"),
        text=text, conversation_id=body.get("conversation_id", "default"), raw=body,
    )
    reply = await handle_message(
        inbound, request.app.state.checkpointer, request.app.state.store,
        tenant_id=tenant_id,                     # 租户身份贯穿到会话隔离
    )
    return JSONResponse({"code": 0, "reply": reply})
