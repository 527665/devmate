"""channels/cli.py —— CLI 渠道：终端里跟 DevMate 对话

直接调本机服务的 HTTP 接口（需先启动 api.app）。
用同一个 --session 多次调用即可续聊（thread_id 由 (cli, session) 稳定派生）。

服务端 /webhook/generic 需要 X-API-Key（DEVMATE_API_KEYS 里的任意一个 key），
这里按 ① --api-key 参数 ② DEVMATE_CLI_API_KEY 环境变量 ③ .env 里第一个 key 的顺序取。

运行：uv run python -m channels.cli --session mywork "帮我看下订单服务的定价逻辑"
"""
import argparse
import asyncio

import httpx

BASE = "http://localhost:8000"


def resolve_api_key(cli_value: str | None) -> str:
    """取 API Key：命令行 > 环境变量 > 配置里的第一个（本地开发方便）。"""
    if cli_value:
        return cli_value
    import os

    if os.environ.get("DEVMATE_CLI_API_KEY"):
        return os.environ["DEVMATE_CLI_API_KEY"]
    from infra.settings import get_settings

    keys = list(get_settings().api_keys)
    if not keys:
        raise SystemExit(
            "没有可用 API Key：请在 .env 配 DEVMATE_API_KEYS，或用 --api-key 传入。")
    return keys[0]


async def ask(session: str, text: str, api_key: str) -> None:
    async with httpx.AsyncClient(timeout=300) as client:
        # 走通用 webhook 的 generic 入口（conversation_id=session 实现 CLI 续聊）
        r = await client.post(
            f"{BASE}/webhook/generic",
            headers={"X-API-Key": api_key},
            json={
                "text": text,
                "source": "cli",
                "conversation_id": f"cli:{session}",
            },
        )
        data = r.json()
        if r.status_code == 401:
            print("鉴权失败(401)：API Key 不对，检查 .env 的 DEVMATE_API_KEYS")
            return
        print("\nDevMate:", data.get("reply", data))


def main():
    p = argparse.ArgumentParser(description="DevMate CLI")
    p.add_argument("--session", default="default", help="会话名（同名续聊）")
    p.add_argument("text", help="要对 DevMate 说的话")
    p.add_argument("--api-key", default=None, help="X-API-Key（默认取 .env 里第一个）")
    args = p.parse_args()
    asyncio.run(ask(args.session, args.text, resolve_api_key(args.api_key)))


if __name__ == "__main__":
    main()
