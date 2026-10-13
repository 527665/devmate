# DevMate —— 变更日志

按模块切分提交，方便他人（和面试官）按时间线读懂这个项目的成长路径。

## Unreleased

- `feat(agent)`：DeepAgents harness 装配（工具、子代理、skills/memory、权限）
- `feat(sandbox)`：Docker 加固沙箱 + 隔离优先沙箱池（用完即弃）
- `feat(middleware)`：成本计量 / 工具审计 / LLM 并发闸 / 模型 fallback
- `feat(gateway)`：FastAPI 网关、API Key 鉴权、按租户限流（SlowAPI + Redis）
- `feat(channels)`：飞书长连接、Web、CLI、Webhook 四入口统一网关
- `feat(tasks)`：arq + Redis 异步长任务、幂等去重
- `feat(obs)`：Prometheus 17 项指标、Grafana 三看板 JSON、LangSmith 追踪
- `test(loadtest)`：locust 压测脚本（/readyz 100 并发 3360 请求 0 失败）
- `docs`：README（架构图、快速开始、实测数据、已知限制）
