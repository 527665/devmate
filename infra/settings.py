"""DevMate 的统一配置入口。

应用代码不直接读取环境变量，而是通过 :func:`get_settings` 获取配置。
项目自己的变量使用 ``DEVMATE_`` 前缀；LangSmith 变量保留官方命名，方便
LangChain/LangSmith SDK 自动识别，同时也纳入本配置对象统一管理。
"""

import os
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# .env 锚定到项目根目录：无论从哪个工作目录启动进程都能找到配置。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """应用运行所需的配置。

    ``BaseSettings`` 会按字段类型读取并校验 ``.env`` 或系统环境变量。
    ``DEVMATE_API_KEY`` 是必填项；其余字段提供适合当前本地开发环境的默认值。
    """

    model_config = SettingsConfigDict(
        env_prefix="DEVMATE_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== 模型 =====
    # DashScope 提供 OpenAI 兼容接口，所以 provider 使用 openai。
    model_name: str = "qwen3-max"
    model_provider: str = "openai"
    api_key: SecretStr
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 模型思考力度（reasoning_effort）：low=少思考省 token，high=深思费 token。
    # OpenAI 兼容接口参数，qwen3 系列支持；qwen-turbo 这类非思考模型会忽略它。
    model_reasoning_effort: str = "low"

    # ===== 模型档位（配置化模型注册表） =====
    # 换模型 / 调整分配只改这里，不动业务代码。
    # 教学示例统一用 qwen 系列不同档位；真实可混用 GLM/DeepSeek/Kimi。
    model_tiers: dict[str, str] = {
        "strong": "qwen3.7-max",      # 难任务：规划、写代码、审查
        "standard": "qwen3.7-plus",   # 中等任务：测试
        "cheap": "qwen3.5-flash",     # 简单任务：读代码、总结
    }

    # ===== 连接弹性 =====
    max_retries: int = 12
    timeout: int = 60

    # ===== 持久化底座 =====
    # Postgres（持久化 + 连接池）
    database_url: str = "postgresql://devmate:devmate@localhost:5432/devmate"
    pg_pool_min: int = 2
    pg_pool_max: int = 20

    # Redis（arq / 限流会用到）
    redis_url: str = "redis://localhost:6379/0"
    redis_pool_max: int = 20

    # ===== 飞书 =====
    # .env 里写 DEVMATE_FEISHU_*，pydantic-settings 自动映射到下面字段。
    # encrypt_key / verification_token：长连接模式可留空；Webhook 回调模式才用。
    feishu_app_id: str = ""
    feishu_app_secret: SecretStr = SecretStr("")
    feishu_encrypt_key: str = ""
    feishu_verification_token: str = ""

    # ===== 网关多租户 =====
    # 形如 {"key-tenant-a": "tenant-a", "key-tenant-b": "tenant-b"}，
    # .env 里 DEVMATE_API_KEYS 用单行 JSON 填；留空表示网关暂不鉴权。
    api_keys: dict[str, str] = {}

    # ===== 沙箱隔离 =====
    # 换隔离方案只改这里：provider 选谁家的车间，runtime 选车间的加固等级。
    sandbox_provider: str = "docker"                # docker（自托管）/ daytona（外部托管）
    sandbox_runtime: str = "runc"                   # runc（加固容器）/ runsc（gVisor）/ kata（microVM）
    sandbox_image: str = "devmate-sandbox:latest"  # 用 sandbox/Dockerfile 构建这个镜像
    sandbox_workdir: str = "/home/agent"            # 容器内工作目录（tmpfs，可写）
    sandbox_mem_limit: str = "512m"                 # 内存上限，防吃爆宿主内存
    sandbox_pids_limit: int = 256                   # 进程上限，防 fork 炸弹
    sandbox_cpus: str = "1.0"                       # CPU 上限，防一个任务占满 CPU
    sandbox_pool_size: int = 4                      # 沙箱池并发上限（第 5 节）

    # ===== 并发与限流 =====
    max_concurrent_llm: int = 8                     # LLM 并发调用上限（第 6 节）
    ratelimit_enabled: bool = True                  # 限流开关；压测时 .env 设 DEVMATE_RATELIMIT_ENABLED=false 关掉

    # ===== 运行 =====
    log_level: str = "INFO"

    # ===== MCP server 配置 =====
    # 运维配置：增减 server / 改 url / 配鉴权都不用动业务代码。
    # 形如 {"server名": {"transport": "http", "url": "...", "headers": {...}}}
    #      {"server名": {"transport": "stdio", "command": "python", "args": ["/abs/path.py"]}}
    # 默认空字典 = 不接任何 MCP；实际项目里可从环境变量 JSON 或单独的 toml 读。
    mcp_servers: dict[str, dict] = {}

    # ===== LangSmith 追踪 =====
    # LangSmith 官方环境变量不使用 DEVMATE_ 前缀，因此用别名读取。
    langsmith_tracing: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "LANGSMITH_TRACING",
            "DEVMATE_LANGSMITH_TRACING",
        ),
    )
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        validation_alias=AliasChoices(
            "LANGSMITH_ENDPOINT",
            "DEVMATE_LANGSMITH_ENDPOINT",
        ),
    )
    langsmith_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LANGSMITH_API_KEY",
            "DEVMATE_LANGSMITH_API_KEY",
        ),
    )
    langsmith_project: str = Field(
        default="devmate",
        validation_alias=AliasChoices(
            "LANGSMITH_PROJECT",
            "DEVMATE_LANGSMITH_PROJECT",
        ),
    )


def _export_langsmith_env(s: "Settings") -> None:
    """把 LangSmith 变量写回 os.environ（核心步骤）。

    关键：LangSmith 库只认系统环境变量，不读 Settings 对象——所以必须写回 os.environ。
    只在开启 tracing 时写，避免没配 key 时设一堆空变量。
    """
    if not s.langsmith_tracing:
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    key = s.langsmith_api_key.get_secret_value() if s.langsmith_api_key else ""
    if key:
        os.environ["LANGSMITH_API_KEY"] = key
    os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = s.langsmith_endpoint


# ===== 模块级执行：import 这个模块时就把环境变量写好 =====
# 放模块级（不只在 get_settings 里）是为了保证：任何代码一旦 import infra.settings，
# LangSmith 变量就立刻进环境，且通常早于 langchain/langgraph 真正使用追踪。
_settings = Settings()
_export_langsmith_env(_settings)


def get_settings() -> Settings:
    """返回进程内复用的配置对象（模块级单例）。"""

    return _settings
