"""构建 DevMate 主 Agent（整合版）。

模型配置来自 ``infra.settings``；Agent 创建使用 DeepAgents 官方工厂。
业务代码创建 Agent 后，统一通过 ``ainvoke`` 或 ``astream`` 异步调用。

本地开发使用 ``FilesystemBackend``，让 Agent 的文件工具作用于项目根目录，
并接入 skills 与常驻记忆。

安全提示：``FilesystemBackend`` 仅适用于本地开发/教学；
生产 / Web 服务必须改用沙箱隔离方案。
新增 ``build_team_agent``：五子代理团队 + Daytona 沙箱后端。
"""

from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver

from infra.logging import get_logger
from infra.settings import get_settings
from infra.fallback import build_fallback_middleware
from middleware.audit import ToolAuditMiddleware
from middleware.concurrency import LLMConcurrencyMiddleware
from middleware.context import RequestContextMiddleware
from middleware.cost import CostMeterMiddleware
from sandbox.manager import get_or_create_sandbox_backend
from subagents.profiles import build_subagents
from tools.mcp import MCPManager
from tools.registry import get_tools


logger = get_logger()


# agent/main.py 位于项目根目录的 agent/ 下，所以 parents[1] 就是项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]


# 这里只描述 DevMate 的业务领域要求；文件读写、任务规划等通用能力
# 由 DeepAgents 自带的系统提示词和工具负责；团队规范见 skills 与常驻记忆。
DEVMATE_SYSTEM_PROMPT = """你是 DevMate，一个严谨的研发助手，服务于一个使用 Python、FastAPI、LangChain、LangGraph、DeepAgents、PostgreSQL 和 Redis 的开发团队。

工作准则：
- 动手前先用 write_todos 写出清晰的任务清单，并随进展更新它。
- 写代码遵循团队规范（见你已加载的 skills 与常驻记忆文件），不臆测，信息不足先用 read_file / grep 读取相关文件。
- 每次改动后，用一两句话说明改了什么、为什么这么改。
"""


def build_model():
    """根据当前配置创建模型；更换模型时只需修改 ``.env``。"""

    settings = get_settings()
    return init_chat_model(
        model=settings.model_name,
        model_provider=settings.model_provider,
        api_key=settings.api_key.get_secret_value(),
        base_url=settings.base_url,
        temperature=0,
        reasoning_effort=settings.model_reasoning_effort,
        max_retries=settings.max_retries,
        timeout=settings.timeout,
    )


def build_backend() -> FilesystemBackend:
    """构建本地文件后端，并把 Agent 限制在项目根目录内。

    ``virtual_mode=True`` 让 Agent 使用以 ``/`` 开头的虚拟路径；
    这些路径会映射到 ``PROJECT_ROOT``，并阻止 ``..``、``~`` 越界。
    这只是路径边界保护，不是进程或网络沙箱。
    """

    return FilesystemBackend(
        root_dir=PROJECT_ROOT,
        virtual_mode=True,
    )


# ===== 沙箱后端按配置可插拔 =====
# （get_settings / get_logger / logger 文件顶部已有，不重复导入）
async def build_sandbox_backend():
    """按 DEVMATE_SANDBOX_PROVIDER 选用沙箱后端，返回 (backend, workdir)。

    docker  → 自托管加固容器（本模块，本地可跑）
    daytona → 外部托管沙箱
    两者都是 BaseSandbox，create_deep_agent(backend=...) 一视同仁。
    """
    s = get_settings()
    if s.sandbox_provider == "docker":
        from sandbox.docker_manager import create_one_sandbox, seed_project
        sb = await create_one_sandbox()
        await seed_project(sb)
        return sb, s.sandbox_workdir
    else:  # daytona —— 外部托管那套
        from sandbox.manager import get_or_create_sandbox_backend
        backend, sandbox, client, workdir = get_or_create_sandbox_backend("default")
        return backend, workdir


def build_agent(
    checkpointer=None,
    store=None,
    user_id: str = "anonymous",
    channel: str = "api",
):
    """构建并返回已编译的 DevMate LangGraph Agent（服务化版）。

    ``async`` 体现在调用阶段：后续使用 ``agent.ainvoke`` 或
    ``agent.astream``，而不是把模型请求放进同步调用中。

    服务化：checkpointer/store 由外部（lifespan）传入并复用。
    不传时退化为内存（仅供脚本/测试），传入则用 Postgres 持久化。

    Args:
        checkpointer: 外部传入的持久化后端（lifespan 的 AsyncPostgresSaver）。
        store: 外部传入的长期记忆后端（lifespan 的 AsyncPostgresStore）。
        user_id: 调用方用户标识，透传给请求上下文中间件。
        channel: 请求来源渠道（cli / api / web 等）。
    """

    settings = get_settings()
    agent = create_deep_agent(
        model=build_model(),
        system_prompt=DEVMATE_SYSTEM_PROMPT,
        backend=build_backend(),
        # 路径相对 backend 的 root（= PROJECT_ROOT），必须用正斜杠（官方要求）。
        skills=["/skills"],
        # 修复（方案 A）：memory 全局共享但只读 ——
        # 不收紧时任何租户都能让 agent 写全局记忆，跨租户泄密（test_tenant_isolation 抓到）。
        permissions=[
            FilesystemPermission(operations=["read"], paths=["/memory/**"], mode="allow"),
            FilesystemPermission(operations=["write"], paths=["/memory/**"], mode="deny"),
        ],
        memory=["/memory/devmate-memory.md"],
        # 新增：按需装配工具（git / search / test）。
        tools=get_tools("git", "search", "test"),
        middleware=[
            # 列表越靠前 = 越外层；上下文中间件放最前，
            # 让它的 before_agent 最先跑、最早注入身份。
            RequestContextMiddleware(user_id=user_id, channel=channel),
            ToolAuditMiddleware(),
            CostMeterMiddleware(),
            LLMConcurrencyMiddleware(),   # ← 限并发 LLM 调用
            build_fallback_middleware(),  # ← 主模型失败退备用
        ],
        checkpointer=checkpointer,  # 外部传入（lifespan 的 AsyncPostgresSaver）
        store=store,  # 外部传入（lifespan 的 AsyncPostgresStore）
    )
    logger.info(
        "DevMate 构建完成：model={}:{}，已装配 git/search/test 工具 + skills/memory/中间件",
        settings.model_provider,
        settings.model_name,
    )
    return agent


async def build_agent_with_mcp(user_id: str = "anonymous", channel: str = "cli"):
    """带 MCP 工具的构建版本。MCP 工具需 await 获取，故本函数是 async。

    新增：不破坏原同步 build_agent，按需选用。
    """

    mcp_tools = await MCPManager.from_settings().get_tools()  # 容错地取回 MCP 工具
    agent = create_deep_agent(
        model=build_model(),
        system_prompt=DEVMATE_SYSTEM_PROMPT,
        backend=build_backend(),
        # 路径相对 backend 的 root（= PROJECT_ROOT），必须用正斜杠（官方要求）。
        skills=["/skills"],
        # 修复（方案 A）：memory 全局共享但只读 ——
        # 不收紧时任何租户都能让 agent 写全局记忆，跨租户泄密（test_tenant_isolation 抓到）。
        permissions=[
            FilesystemPermission(operations=["read"], paths=["/memory/**"], mode="allow"),
            FilesystemPermission(operations=["write"], paths=["/memory/**"], mode="deny"),
        ],
        memory=["/memory/devmate-memory.md"],
        # 自定义工具 + MCP 工具。
        tools=get_tools("git", "search", "test") + mcp_tools,
        middleware=[
            RequestContextMiddleware(user_id=user_id, channel=channel),
            ToolAuditMiddleware(),
            CostMeterMiddleware(),
        ],
        checkpointer=MemorySaver(),
    )
    logger.info("DevMate（含 MCP）构建完成：MCP 工具 {} 个", len(mcp_tools))
    return agent


def build_team_agent(thread_id: str, user_id: str = "anonymous", channel: str = "cli"):
    """五子代理团队 + 沙箱后端（已 seed 项目到沙箱工作目录）。

    - backend 用沙箱（自动提供文件工具 + execute），取代原先直接读写宿主文件的 FilesystemBackend；
    - 沙箱在新建时已被 seed 进 tests/ skills/ memory/（见 sandbox/manager.py）；
    - skills/memory 指向沙箱工作目录下 seed 后的真实路径（workdir/skills 等）；
    - subagents 注入五个专职子代理；
    - 密钥（API key）只在主进程的 build_model() 里用，绝不传进沙箱。

    返回 (agent, sandbox, client) —— 后两者用于用完后清理。
    """

    # workdir 是沙箱工作目录（Daytona 默认 /home/daytona），项目就 seed 在它下面。
    sandbox_backend, sandbox, client, workdir = get_or_create_sandbox_backend(thread_id)

    agent = create_deep_agent(
        model=build_model(),
        system_prompt=DEVMATE_SYSTEM_PROMPT + (
            "\n\n【执行环境与路径规则——必须严格遵守】"
            f"\n你运行在一个沙箱里，项目代码已位于 `{workdir}/` 下（含 `{workdir}/tests/`）。"
            "\n⚠️ 所有文件操作（write_file/edit_file/read_file）和命令（execute）"
            f"都【必须】使用以 `{workdir}/` 开头的【绝对路径】。"
            f"\n✅ 正确：写测试到 `{workdir}/tests/test_pricing.py`、"
            f"跑测试 `cd {workdir} && python -m pytest -q`。"
            "\n❌ 错误（会因权限被拒绝，绝不要这样）："
            "`/test_pricing.py`、`test_pricing.py` 这类根路径或相对路径。"
            f"\n如果你不确定某文件在哪，先用 `ls {workdir}` 查看，再用绝对路径操作。"
            "\n\n你是团队负责人：对复杂 Issue，先用 task() 委派给 planner 规划，"
            "再依次委派 researcher/coder/tester/reviewer。你只做协调，不亲自写大量代码。"
            "委派时，把上面的【绝对路径规则】一并转达给子代理。"
        ),
        backend=sandbox_backend,                # ← 沙箱后端：自动带 execute
        subagents=build_subagents(workdir),     # ← 五个专职子代理（workdir 写进各自 prompt）
        skills=[f"{workdir}/skills"],           # ← seed 后的真实路径（不再是空的 /skills/）
        memory=[f"{workdir}/memory/devmate-memory.md"],
        middleware=[
            RequestContextMiddleware(user_id=user_id, channel=channel),
            ToolAuditMiddleware(),
            CostMeterMiddleware(),
        ],
        checkpointer=MemorySaver(),
    )
    logger.info(
        "DevMate 团队版构建完成：5 子代理 + 沙箱后端（项目已 seed 到 {}）",
        workdir,
    )
    return agent, sandbox, client
