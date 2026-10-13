"""loadtest/locustfile.py —— DevMate 服务压测（小规模教学版）

依赖：uv add locust（已装）
Windows 注意：跑前先 $env:PYTHONUTF8="1"，否则 locust 读 pyproject.toml 报 GBK 解码错
（实测：locust --version 直接挂 'gbk' codec can't decode byte 0x80）。
Web UI： $env:PYTHONUTF8="1"; .\\.venv\\Scripts\\locust.exe -f loadtest/locustfile.py --host http://localhost:8000
然后浏览器开 http://localhost:8089 设并发开压。
铁律：locust 只做独立压测进程，绝不可 import 进 web/worker（gevent monkey-patch 污染）。
"""
from locust import HttpUser, task, between


class LightUser(HttpUser):
    """压轻端点测纯框架吞吐——不触发 agent、不烧钱、不撞限流。"""
    wait_time = between(1, 2)

    @task
    def health(self):
        self.client.get("/readyz")


class JobUser(HttpUser):
    """压 /jobs 端到端——会真跑 agent、烧钱，必须先关限流（3.2），只做小规模。"""
    wait_time = between(2, 5)

    @task
    def submit_job(self):
        self.client.post("/jobs", json={"text": "用一句话解释幂等性", "user_id": "lt"},
                         headers={"X-API-Key": "key-a"})
