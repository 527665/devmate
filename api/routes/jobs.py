"""api/routes/jobs.py —— 方案 B：arq 异步提交（入队即返回）+ 查结果"""
from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from gateway.auth import require_api_key
from gateway.limiter import limiter

router = APIRouter(prefix="/jobs", tags=["jobs-arq"])


@router.post("")
@limiter.limit("10/minute")               # ← 必须在 @router.post 下方；本函数签名已显式带 request: Request
async def submit_async(
    req_body: dict,
    request: Request,
    tenant_id: str = Depends(require_api_key),   # 先鉴权再入队：没 key 连队都进不去
):
    """提交到 arq 队列，立即返回 job_id。

    鉴权 + 限流两道都在"花钱"之前：401 和 429 都不会产生一次模型调用。
    """
    from tasks.queue import enqueue_issue
    payload = {
        "channel": req_body.get("channel", "api"),
        "user_id": req_body.get("user_id", "anonymous"),
        "text": req_body["text"],
        "conversation_id": req_body.get("conversation_id", uuid4().hex),
        "tenant_id": tenant_id,                  # 租户身份贯穿到会话隔离
    }
    job_id = await enqueue_issue(request.app.state.arq_redis, payload)
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
async def query_job(job_id: str, request: Request):
    from tasks.queue import get_job_result
    return await get_job_result(request.app.state.arq_redis, job_id)
