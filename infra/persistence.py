"""infra/persistence.py —— 建异步持久化资源（saver/store），供服务与独立进程共用

早期把建池/saver/store 直接写在 FastAPI lifespan 里。但飞书长连接接收端是
独立进程、不经过 lifespan，需要自己建一份。于是把这段抽出来两边复用——
这也修正了"独立进程 from api.app import app 拿不到 app.state"的错误接法。

修正（两个缺口一起修）：
- 缺口 1：这里原本没有 register_all_profiles()，导致独立进程（飞书长连接、
  以及 arq worker）跑出来的 agent 不应用 qwen 的 Harness Profile，
  与 Web 服务进程行为不一致。现在把注册收进本函数，所有走这条路的进程自动一致。
- 缺口 2：api/app.py 原本自己内联了一份建资源逻辑，与本函数并存 = 两份重复实现，
  改一处忘另一处就会静默分叉。现在 app.py 也改成调用本函数，全项目只剩一份。

注意：Redis 不在这里建。Redis 目前只有 Web 服务侧要用（arq 上线后才真正用上），
独立进程不需要就不建——惰性对象虽不占资源，但会占读代码的人的认知。
"""
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from infra.db import build_pg_pool
from infra.logging import get_logger
from profiles import register_all_profiles

logger = get_logger()


async def open_persistence():
    """注册 Harness Profile + 打开连接池 + 构造并 setup saver/store。

    返回 (pool, checkpointer, store)。调用方负责在退出时 await pool.close()。

    为什么 register_all_profiles() 放这儿：它必须在【任何 agent 构建之前】执行。
    本函数是所有进程（Web 服务 / 飞书长连接 / 以后的 worker）建资源的唯一入口，
    放在这里就等于"只要你要资源，就一定已经调校过模型"。
    重复调用安全：实测连调 3 次，system_prompt_suffix 长度不变（56）、
    excluded_middleware 集合不变 —— 因为合并语义对字符串字段是"后来者覆盖"、
    对 excluded 是"集合并集"，不是拼接叠加。
    """
    # ① 先注册 profile，再建任何可能被 agent 用到的东西（顺序有讲究）
    register_all_profiles()

    # ② 建并打开 Postgres 连接池（一个池，同时喂 saver 和 store）
    pool = build_pg_pool()
    await pool.open()
    logger.info("Postgres 连接池已打开")

    # ③ 同一个池构造异步 saver / store，并 setup（首次建表；已存在则跳过，幂等）
    checkpointer = AsyncPostgresSaver(pool)
    store = AsyncPostgresStore(pool)
    await checkpointer.setup()
    await store.setup()
    logger.info("AsyncPostgresSaver / AsyncPostgresStore 已 setup")

    return pool, checkpointer, store