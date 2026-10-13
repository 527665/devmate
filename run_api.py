"""本地开发启动入口：先设 Windows 事件循环策略，再手动驱动 uvicorn Server。

用法（项目根目录）：uv run python run_api.py
原因：uvicorn 0.52+ 在 Windows 上硬编码返回 ProactorEventLoop，
而 psycopg 异步模式要求 SelectorEventLoop，必须绕过 uvicorn.run() 手动控制事件循环。
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

config = uvicorn.Config("api.app:app", host="127.0.0.1", port=8000)
server = uvicorn.Server(config)
asyncio.run(server.serve())
