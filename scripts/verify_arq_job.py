"""scripts/verify_arq_job.py —— 方案 B：arq 任务提交 + 轮询
三进程都要起：Redis、arq worker、uvicorn 服务。
运行：uv run python -m scripts.verify_arq_job
"""
import asyncio
import httpx

from infra.settings import get_settings

BASE = "http://localhost:8000"


async def main():
    keys = list(get_settings().api_keys)
    if not keys:
        raise SystemExit("请先在 .env 配 DEVMATE_API_KEYS，否则 /jobs 一律 401")
    H = {"X-API-Key": keys[0]}
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(f"{BASE}/jobs", headers=H,
                              json={"text": "用一句话解释任务队列。", "user_id": "alice"})
        if r.status_code != 200:
            print("提交失败：", r.status_code, r.text[:200])
            return
        job_id = r.json()["job_id"]
        print("① 已提交，job_id =", job_id, "（服务秒级返回，执行在 worker 进程）")
        for i in range(60):
            await asyncio.sleep(2)
            data = (await client.get(f"{BASE}/jobs/{job_id}")).json()
            print(f"   第{i+1}次：status={data['status']}")
            if data["status"] == "complete":
                print("\n② 结果：", data.get("result", {}).get("reply", "")[:200])
                break


if __name__ == "__main__":
    asyncio.run(main())
