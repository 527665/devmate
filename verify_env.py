"""verify_env.py —— 启动期自检。

检查项目配置、PostgreSQL 和 Redis 是否可以正常使用。
"""

import asyncio
import sys
from urllib.parse import urlsplit, urlunsplit

import psycopg
import redis.asyncio as aioredis

from infra.settings import get_settings


def mask_connection_url(url: str) -> str:
    """隐藏连接串中的密码，避免自检输出泄露凭据。"""

    parsed = urlsplit(url)
    if parsed.password is None:
        return url

    username = parsed.username or ""
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{username}:***@{hostname}{port}"
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


async def check_postgres(url: str) -> str:
    """异步连接 PostgreSQL，并返回数据库版本信息。"""

    async with await psycopg.AsyncConnection.connect(url) as connection:
        cursor = await connection.execute("SELECT version();")
        row = await cursor.fetchone()

    if row is None:
        raise RuntimeError("PostgreSQL 没有返回版本信息")
    return str(row[0])


async def check_redis(url: str) -> bool:
    """异步连接 Redis，并执行 PING。"""

    client = aioredis.from_url(url)
    try:
        return bool(await client.ping())
    finally:
        await client.aclose()


async def main() -> int:
    settings = get_settings()

    print("配置加载成功：")
    print(f"   model    = {settings.model_provider}:{settings.model_name}")
    print(f"   api_key  = {settings.api_key}")
    print(f"   postgres = {mask_connection_url(settings.postgres_url)}")
    print(f"   redis    = {settings.redis_url}")

    version = await check_postgres(settings.postgres_url)
    print(f"Postgres 连通：{version[:40]}...")

    pong = await check_redis(settings.redis_url)
    print(f"Redis 连通：ping = {pong}")

    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(
            f"启动期自检失败：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
