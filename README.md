# DevMate

> 开源、可自部署、多模型路由、多渠道接入的 AI 研发助手：给它一个 Issue，它在 Docker 加固沙箱里读代码、改代码、跑测试，全程可观测、可评测、可限流、可回退。

基于 DeepAgents + LangGraph 构建，覆盖「Agent Harness -> 服务化 -> 多渠道 -> 弹性 -> 可观测 -> 压测容量 -> 离线评测」的完整工程链路。本文档中所有性能数字均来自本机实测，非估算。

---

## 核心亮点（全部实测背书）

| 能力 | 一句话 | 实测证据 |
|---|---|---|
| 三入口同脑 | CLI / Web 前端 / 飞书 共用同一 Agent 与会话记忆 | 三渠道端到端各跑通一单，飞书日志实锤 channel=feishu + 新模型生效 |
| 隔离沙箱 | 每任务一个全新加固容器，用完即销毁 | --network none / --cap-drop ALL / --read-only / tmpfs 工作目录，池上限 4 |
| 异步削峰 | FastAPI 收单 4ms，arq worker 后台执行 | 压测：提交 P50=4ms，任务执行 P50≈5s，排队直方图完整还原三段延迟 |
| 全链路可观测 | Prometheus 双 target 采集 + Grafana 三看板 + LangSmith 追踪 | 17 块指标板；100 并发压测 3360 请求 0 失败，看板与 locust 三方对账一致 |
| 成本进血液 | 每次模型调用记 token/元，按档位打标签 | 单任务成本 0.0135 元（手算与埋点分毫不差），220 单累计 2.97 元 |
| 弹性三件套 | 限流（租户分桶、Redis 共享、开关可控）+ 模型 fallback + Redis 幂等去重 | 限流 10/min 实测第 11 发 429；开关 false 后 288 发全放行 |
| 离线评测 | 沙箱 pytest 客观判分 + LLM-as-judge + 重复取均值 + 基线回归对比 | 执行类 0/1 判分 + AST 静态检查，开放题裁判打分，退化自动标警告 |

## 架构

```
                ┌─────────── 入口层（多渠道归一化 InboundMessage）───────────┐
                │ channels/cli.py   web/index.html   channels/feishu_ws.py  │
                │ （HTTP 拉型）     （generic 降级）  （wss 长连接推型）       │
                └──────────────┬──────────────────────────┬─────────────────┘
                               ▼                          ▼
                  ┌─ api/app.py (FastAPI) ─┐   ┌─ tasks/worker.py (arq) ──────┐
                  │ gateway/auth.py APIKey │   │ 独立进程，指标端口 :8002       │
                  │ gateway/limiter.py 限流│   │  ┌─ agent/main.py ──────────┐ │
                  │ POST /jobs 入队即返     │──▶│  │ DeepAgents create_agent  │ │
                  │ POST /issues 同步执行   │队列│  │ 中间件栈: context/cost/  │ │
                  │ /webhook/* 渠道回调     │   │  │ audit/concurrency(8)     │ │
                  │ /metrics 指标窗户       │   │  │ 工具: git/search/test     │ │
                  └───────────┬────────────┘   │  │ skills + 常驻记忆         │ │
                              │                │  │ sandbox/pool.py 信号量池  │ │
                  ┌───────────▼──────────────┐ │  │ fallback 主→备→备         │ │
                  │ Redis: arq队列/幂等/限流  │ │  └──────┬───────────────────┘ │
                  │ Postgres: checkpointer / │◀┤─────────┤ Docker 加固沙箱×4    │
                  │   store(pgvector)/结果   │ │         ▼                     │
                  └──────────────────────────┘ │   容器内执行+pytest 后销毁     │
                                               └───────────────────────────────┘
   infra/llm_router.py 三档模型路由
   obs/metrics.py 17 块黑板 ← Prometheus(9090) 每 15s 抄 :8000 + :8002 → Grafana(3000)
   LangSmith 追踪（trace 树，零代码接入）
```

延迟拆三段（本项目监控设计的核心）：agent_queue_wait（等位）→ agent_task_duration（端到端）→ agent_llm_call_duration（单轮模型）。三段各自成 Histogram，瓶颈定位不靠猜。

## 技术栈

| 层 | 选型 |
|---|---|
| Agent Harness | DeepAgents 0.7.13（todo/子代理/文件系统/技能/常驻记忆） |
| 编排 | LangGraph（AsyncPostgresSaver checkpointer + pgvector store，thread_id 会话续聊） |
| 模型 | 主 deepseek-v4-flash-0731；三档路由 strong=qwen3.7-max / standard=qwen3.7-plus / cheap=qwen3.5-flash（OpenAI 兼容接口，失败自动 fallback） |
| 服务 | FastAPI + uvicorn（Windows SelectorEventLoop 适配，见 run_api.py） |
| 队列 | arq（Redis），worker 独立进程 |
| 存储 | Postgres 17 (pgvector) + Redis 7 |
| 渠道 | 飞书长连接（lark-oapi）/ 通用 webhook / CLI / 零依赖 Web 单页 |
| 弹性 | SlowAPI 限流（租户分桶 + Redis 共享 + 配置开关）、Redis SET NX 幂等、ModelFallbackMiddleware |
| 沙箱 | Docker 加固（network=none / cap-drop ALL / no-new-privileges / read-only / tmpfs / 内存 512m / pids 256 / cpu 1.0 / 非 root），信号量池，用完即弃 |
| 可观测 | prometheus_client 17 指标 + Grafana 3 看板（JSON 即代码）+ LangSmith |
| 压测 | locust（headless + CSV，独立进程防 gevent 污染） |
| 评测 | 沙箱 pytest 客观判分 + AST 静态检查 + LLM-as-judge + 基线回归 |
| 工程 | uv 包管理、pydantic-settings（DEVMATE_ 前缀）、loguru 结构化日志 |

## 快速开始（Windows PowerShell）

### 1. 依赖

```powershell
uv sync

# 起基础设施（已在跑可跳过）
docker run -d --name devmate-redis -p 6379:6379 redis:7
docker run -d --name devmate-postgres -p 5432:5432 `
  -e POSTGRES_USER=devmate -e POSTGRES_PASSWORD=*** -e POSTGRES_DB=devmate `
  pgvector/pgvector:pg17

# 构建沙箱镜像（sandbox/Dockerfile）
docker build -t devmate-sandbox:latest sandbox/
```

### 2. 配置 .env

```ini
# 模型（OpenAI 兼容端点，示例为 DashScope）
DEVMATE_MODEL_NAME=deepseek-v4-flash-0731
DEVMATE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DEVMATE_API_KEY=***

# 持久化：字段名是 database_url（键 DEVMATE_DATABASE_URL，默认值已对齐上面的容器）
DEVMATE_REDIS_URL=redis://localhost:6379/0

# 网关：API key -> 租户映射（单行 JSON）
DEVMATE_API_KEYS={"key-a":"tenant-a"}
DEVMATE_RATELIMIT_ENABLED=true        # 压测时临时 false，压完必须改回

# 可观测（LangSmith 官方键名，无需前缀）
DEVMATE_LANGSMITH_TRACING=true
DEVMATE_LANGSMITH_API_KEY=***
```

### 3. 起服务（三个终端）

```powershell
uv run python run_api.py                # ① API :8000（勿裸 uvicorn，Windows 事件循环坑）
uv run arq tasks.worker.WorkerSettings  # ② worker（自带指标端口 :8002）
uv run python -m channels.feishu_ws     # ③ 飞书长连接（可选，需配凭证）
```

### 4. 验证

```powershell
curl.exe -s http://localhost:8000/readyz    # {"status":"ready"}
uv run python -m channels.cli --session demo "用一句话解释什么是反向代理"
# 浏览器打开 web/index.html（或 uv run python -m http.server 8010 后访问 /web/index.html）
```

### 5. 可观测栈（可选）

```powershell
docker network create devmate-obs
docker run -d --name devmate-prometheus --network devmate-obs -p 9090:9090 `
  -v "${PWD}\obs\prometheus.yml:/etc/prometheus/prometheus.yml:ro" `
  -v devmate-prom-data:/prometheus prom/prometheus:latest `
  --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.retention.time=90d
docker run -d --name devmate-grafana --network devmate-obs -p 3000:3000 `
  -v devmate-grafana-data:/var/lib/grafana grafana/grafana-oss:latest
# Grafana 加数据源 http://devmate-prometheus:9090，导入 obs/dashboard-*.json 三看板
```

## 项目结构

```
agent/        main.py：create_deep_agent 装配（模型/工具/中间件/skills/常驻记忆）
api/          app.py：FastAPI 装配 + lifespan + /readyz /metrics；routes/：issues(同步) jobs(arq)
channels/     base/handler/session：InboundMessage 归一化 + (tenant,channel,conv)->thread_id
              cli.py / feishu.py / feishu_ws.py / webhook.py（challenge 握手 + 幂等）
gateway/      auth.py APIKey 鉴权；limiter.py SlowAPI 租户分桶 + 开关
infra/        settings.py(pydantic-settings) llm_router.py(三档) persistence.py(checkpointer/store)
              fallback.py idempotency.py logging.py
middleware/   context.py(request_id 注入) cost.py(token/成本埋点) audit.py(工具审计)
              concurrency.py(LLM 并发闸 Semaphore)
sandbox/      docker_manager.py(建/seed/拆加固容器+只读沙箱) docker_sandbox.py(BaseSandbox 实现)
              pool.py(信号量池，用完即弃)
tasks/        queue.py(入队塞 enqueued_at) worker.py(arq + 指标端口 + cron 采队列长)
obs/          metrics.py(17 块黑板 + PrometheusMiddleware) prometheus.yml dashboard-*.json(看板即代码)
eval/         code_cases + exec_scorer(沙箱 pytest 0/1 判分 + AST 静态检查)
              dataset + judge(LLM 裁判) run_eval(重复 x3 取均值，基线回归对比)
loadtest/     locustfile.py（LightUser 压 /readyz 零成本；JobUser 压 /jobs 端到端）
tools/        git_tools / search_tools / test_tools + registry + mcp
subagents/    reviewer.py（FilesystemPermission 物理只读评审员）
skills/ memory/  团队规范技能 + 常驻记忆（DeepAgents 注入）
scripts/      各能力验证脚本（agent / 中间件 / 沙箱 / 路由 / 队列 / 限流 / 幂等）
tests/ web/   pytest；零依赖单页前端
```

## 实测数据（压测与容量）

| 场景 | 结果 |
|---|---|
| 框架吞吐（locust 100 并发 x 60s 压 /readyz） | 3360 请求 0 失败；服务端 P50=2ms、P99≈48ms；RPS≈66（受 wait_time 参数封顶，非系统上限） |
| 端到端（20 并发 x 60s 压 /jobs，限流关闭） | 提交 P50=4ms；worker 吞吐≈1.8 单/s（max_jobs=10 x 单任务 5.55s）；队列峰值 209；排队 P50≈41s、P99≈118s |
| 成本 | 单任务≈0.0135 元；220 单累计 2.97 元（埋点与手算一致） |
| 限流 | 10/min 配置下第 11 发 429，ratelimit_rejected_total{tenant} 同步计数 |
| 容量公式 | 需要 worker 数 = 目标 QPS ÷ (并发÷单任务耗时) x 1.3 余量；20 并发压测下需 4 个 worker 实例 |

读法心得：异步架构下 locust 只量到「挂号」，真实体验看 agent_queue_wait；不同端点 Max 撞车 = 瓶颈在共享层；P50 好看 P95 悬崖 = 一批请求在排队。

## 评测（eval）

- 执行类（编码题）：起沙箱 -> agent 答题 -> 写入隐藏测试 -> pytest 通过=1 否则=0（客观真值），附 AST 静态检查（金额禁用 float、必须有类型注解——量化团队规范）。
- 开放题：LLM-as-judge 按 criteria 打 0-10，JSON 强约束输出，解析失败给 0。
- 回归：每题重复 3 次取均值，与 eval/baseline.json 对比，噪声带 max(std, 0.03)，退化自动标警告（CI 门禁就绪）。

## 已知限制（诚实清单，按优先级）

1. web/index.html 期望的 POST /chat/stream（SSE 真流式）后端未实现，当前走 /webhook/generic 降级 + 前端假打字机。
2. feishu_ws 进程内直接调用 agent，未暴露指标端口——飞书渠道的模型调用不进 Prometheus（修法：start_http_server(8003) + 加 scrape job）。
3. CostMeterMiddleware 未传 tier 参数，fallback 到非 strong 档时成本仍记在 tier="strong" 上；TOKEN_PRICE_PER_1K 为示例价，需按实际计费表更新。
4. agent_fallback_total 指标已注册未接线（库中间件内部行为，需包一层才能埋点）。
5. POST /jobs 空 body 返回 500（req_body["text"] 直接下标），应为 422（缺 Pydantic 请求模型）。
6. agent_queue_length 基线为 1 而非 0：arq cron 自我排期占队列槽位，属统计口径问题非故障。
7. .env 中 DEVMATE_POSTGRES_URL 为无效键（字段名是 database_url），当前靠默认值生效。

## 路线图

- [ ] /chat/stream SSE 真流式（token 级推送）
- [ ] FinOps：按租户预算熔断（成本埋点已就绪，缺策略层）
- [ ] 飞书渠道指标补盲 + HITL 审批流（interrupt）接入生产路径
- [ ] CI：pytest + eval 基线门禁 + 镜像构建
- [ ] 生产部署：Gunicorn 多 worker（PROMETHEUS_MULTIPROC_DIR 分支已写好）+ Nginx 反代 + compose

## 说明

- 性能数字均来自 2026-09 本机（Windows / Docker Desktop / 单实例）实测，换环境需重跑 loadtest/ 校准。
- 密钥全部走 .env（已 gitignore），仓库不携带任何凭证。
