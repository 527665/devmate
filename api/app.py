"""api/app.py —— FastAPI 入口 + lifespan（建池/持久化/挂资源）

工程化核心：
- 建资源统一走 infra.persistence.open_persistence()（和独立进程同一套逻辑，
  不再在本文件内联一份，避免两边静默分叉）；
- setup() 建表（首次）；把 checkpointer/store/redis 挂到 app.state 供全局复用；
- 关闭时统一释放——资源生命周期完全归服务所有，绝不在请求里临时建连。
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from infra.persistence import open_persistence
from infra.redis import build_redis
from infra.logging import get_logger
from profiles import register_all_profiles
from api.schemas import HealthResponse
from api.routes.issues import router as issues_router
from channels.webhook import router as webhook_router    # CLI/Web 渠道共用 /webhook/generic
from api.routes.jobs import router as jobs_router    # /jobs 异步提交 + 查结果
from tasks.queue import get_arq_redis                     # producer 入队用的 arq Redis 池
from gateway.limiter import limiter, _tenant_key          # 限流器（按 tenant 分桶 + Redis 共享计数）
from obs.metrics import PrometheusMiddleware, RATELIMIT_REJECTED   # 埋点 + /metrics

logger = get_logger()

# Windows: psycopg 异步模式要求 SelectorEventLoop（默认 ProactorEventLoop 不兼容）。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ① 注册 Harness Profile——在任何 agent 构建前。
    #    注：open_persistence() 内部也会注册一次；重复调用是安全的
    #    （deepagents 的注册是 additive merge，同名字段后者覆盖前者，
    #      实测连调 3 次 suffix 长度与 excluded 集合都不变），这里显式写一次
    #    是为了让"Web 服务启动时应用了 profile"在本文件里可读。
    register_all_profiles()

    # ② 建并打开 Postgres 连接池 + saver/store + setup（统一逻辑，见 infra/persistence.py）
    pool, checkpointer, store = await open_persistence()

    # ③ Redis 池（故意留在本文件：只有 Web 服务需要 Redis，
    #    飞书长连接/arq worker 不需要，就别挂个永远用不到的对象误导读代码的人）
    redis = build_redis()

    # ③-2 arq 的 Redis 池（producer 侧，只用来入队/查结果）。
    #    注意：和上面 build_redis() 是两个不同的连接池、连的是同一个 Redis 服务——
    #    build_redis() 是 decode_responses=True 的字符串客户端（限流/缓存用），
    #    arq 要自己那套二进制客户端（任务体是 pickle 字节），两者不能互相顶替。
    arq_redis = await get_arq_redis()

    # ④ 挂到 app.state，供所有请求复用
    app.state.pg_pool = pool
    app.state.checkpointer = checkpointer
    app.state.store = store
    app.state.redis = redis
    app.state.arq_redis = arq_redis

    try:
        yield                      # —— 服务运行中 ——
    finally:
        # ⑤ 关闭时统一释放
        await arq_redis.close()
        await redis.aclose()
        await pool.close()
        logger.info("连接池已释放")


app = FastAPI(title="DevMate API", lifespan=lifespan)
# 接进服务：limiter 挂到 app.state；超限自动 429（专用处理器优先于下面的 Exception 兜底）
app.state.limiter = limiter


async def rate_limit_exceeded_with_metric(request: Request, exc: RateLimitExceeded):
    """包一层 SlowAPI 的 429 处理器——先记一笔再交回原处理器出响应。

    标签复用 gateway/limiter.py 的 _tenant_key（和限流分桶同一口径，账和桶对得上）；
    取标签失败也不能挡 429 本身，兜底记 "unknown"。
    """
    try:
        tenant = _tenant_key(request)
    except Exception:
        tenant = "unknown"
    RATELIMIT_REJECTED.labels(tenant).inc()               # 埋点：被限流拒绝
    return _rate_limit_exceeded_handler(request, exc)


app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_with_metric)

# HTTP 层 RED 采集中间件（后加 = 更外层，CORS 也被计量）
app.add_middleware(PrometheusMiddleware)
app.include_router(issues_router)
app.include_router(webhook_router)      # 注册后才有 /webhook/feishu 与 /webhook/generic；不挂这行 -> 一律 404
app.include_router(jobs_router)             # 注册后才有 POST /jobs 与 GET /jobs/{job_id}

# 允许 Web 前端跨域调用（前端用 file:// 或不同端口打开时必须）。
# ⚠ allow_origins=["*"] 只适用于本地开发；上生产必须收紧成具体前端地址，
#   否则任何网页都能驱动你的后端（而 /webhook/generic 是无鉴权的）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # 开发期放开；生产应收紧到你的前端域名
    allow_methods=["*"],
    allow_headers=["*"],
)


# 统一异常处理：不把内部堆栈暴露给客户端
@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    logger.exception("未处理异常：{}", exc)
    return JSONResponse(status_code=500, content={"detail": "内部错误，请稍后重试"})


# 健康检查：liveness（纯探活）
@app.get("/healthz", response_model=HealthResponse)
async def healthz():
    return HealthResponse(status="ok")


# 健康检查：readiness（依赖就绪才 OK）
@app.get("/readyz", response_model=HealthResponse)
async def readyz(request: Request):
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        return JSONResponse(status_code=503, content={"status": "not_ready", "detail": "pool 未就绪"})
    return HealthResponse(status="ready")


# 暴露 /metrics（普通 GET 路由，避开 app.mount 的 307 重定向坑）
@app.get("/metrics")
async def metrics():
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        # 生产多进程（Gunicorn 多 worker）：聚合所有 worker 的 mmap 分片
        from prometheus_client import CollectorRegistry, multiprocess
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        data = generate_latest(registry)
    else:
        data = generate_latest()                  # 本地单进程：默认全局 registry
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
