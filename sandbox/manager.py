"""sandbox/manager.py —— 沙箱接入与生命周期管理（"沙箱即工具"模式）

这个模块负责：把 Agent 的"手脚"放进一个隔离的远程沙箱里。
Agent 在沙箱里读写文件、跑命令（pytest、python 等），而宿主机/主进程保持干净、
不被 Agent 生成的代码污染，密钥也不会泄漏进沙箱。

工程化要点（来自官方 sandboxes 文档）：
- 按 thread_id 隔离：每个会话（一个 Issue）对应一个独立沙箱，get-or-create 复用；
- TTL 自动回收：空闲沙箱由 provider（Daytona）自动清理，避免一直挂着计费；
- 密钥绝不进沙箱：API key 只在主进程 build_model() 里用（见 agent/main.py 与本模块的安全约定）。

注意（高并发）：本模块是"每会话一个沙箱"的基础形态。后续做高并发服务时，
要升级成"沙箱池 + 并发隔离 + 超时回收"，并配合任务队列复用沙箱。
"""

from __future__ import annotations

from pathlib import Path

from infra.logging import get_logger


logger = get_logger()


def _collect_project_files(
    root: Path,
    dest_root: str,
    subdirs: list[str],
    extra_files: list[str],
) -> list[tuple[str, bytes]]:
    """把宿主机项目里要 seed（预置）进沙箱的文件，收集成 upload_files 需要的格式。

    返回值：[(沙箱内绝对路径, 文件字节内容), ...]
    例如：[("/home/daytona/app/pricing.py", b"def price()..."), ...]

    两个硬性约束（官方要求，踩了会报错）：
      1) upload_files 的目标路径必须是【绝对路径】；
      2) 文件内容必须是 bytes（所以下面用 read_bytes() 而不是 read_text()）。

    dest_root 是沙箱的【工作目录】（Daytona 默认 /home/daytona）——
    一定要把项目放到它下面，不要往 / 根目录写：
    沙箱默认用户没有 root 权限，往根目录 mkdir 会直接 'permission denied'。
    """

    items: list[tuple[str, bytes]] = []
    dest_root = dest_root.rstrip("/")  # 去掉结尾斜杠，规范化，避免后面拼出 // 双斜杠

    # 1) 处理指定的子目录：递归遍历里面所有文件并上传。
    #    跳过 __pycache__ 缓存目录和 . 开头的隐藏文件/目录，避免把垃圾传进去。
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and not any(
                part.startswith(".") for part in p.relative_to(root).parts
            ):
                rel = p.relative_to(root).as_posix()
                items.append((f"{dest_root}/{rel}", p.read_bytes()))

    # 2) 处理单独列出的根级文件（比如仓库记忆文件，它不在某个子目录里）。
    for fname in extra_files:
        f = root / fname
        if f.is_file():
            items.append((f"{dest_root}/{fname}", f.read_bytes()))

    return items


def seed_project_into_sandbox(backend, sandbox, root: Path) -> str:
    """把项目源码/技能/记忆 seed（预置）进沙箱（Agent 跑之前必须做这一步）。

    返回：沙箱的工作目录（如 /home/daytona），后面拼 skills/memory 路径要用。
    """

    # 先取沙箱的真实工作目录（Daytona 是 WORKDIR 或用户家目录，通常 /home/daytona）；
    # 不同版本/镜像可能没有这个方法或抛异常，所以用 try 兜底成 /home/daytona。
    try:
        workdir = sandbox.get_workspace_root_dir()
    except Exception:
        workdir = "/home/daytona"
    workdir = (workdir or "/home/daytona").rstrip("/")

    files = _collect_project_files(
        root,
        dest_root=workdir,
        subdirs=["app", "tests", "skills", "memory"],
        extra_files=["AGENTS.md"],
    )
    if not files:
        logger.warning("没有可 seed 的项目文件（app/ tests/ skills/ 都为空？）")
        return workdir

    # 官方 API：upload_files([(绝对路径, bytes), ...])
    backend.upload_files(files)
    logger.info("已 seed {} 个文件进沙箱工作目录 {}", len(files), workdir)
    return workdir


def download_artifacts(backend, sandbox_paths: list[str]) -> dict[str, bytes]:
    """从沙箱取回指定文件（agent 跑完后用）。返回 {沙箱路径: 字节内容}。

    官方 download_files 返回结果含 .path / .content(bytes) / .error。
    """

    out: dict[str, bytes] = {}
    results = backend.download_files(sandbox_paths)
    for r in results:
        if getattr(r, "content", None) is not None:
            out[r.path] = r.content
        else:
            logger.warning(
                "取回失败 {}：{}",
                getattr(r, "path", "?"),
                getattr(r, "error", "?"),
            )
    return out


def get_or_create_sandbox_backend(thread_id: str, project_root: Path | None = None):
    """按 thread_id 获取或创建一个沙箱后端（这里用 Daytona 做示例），新建时顺带 seed 项目。

    返回四元组 (backend, sandbox, client, workdir)：
      - backend  传给 create_deep_agent(backend=...)，让 Agent 拥有文件工具 + execute；
      - sandbox  沙箱对象，用于生命周期管理（stop/delete）；
      - client   Daytona 客户端，同样用于管理/查询；
      - workdir  沙箱工作目录（如 /home/daytona），用于拼 skills / memory 的绝对路径。
    """

    # 延迟到函数内部再 import：避免没装/没用到沙箱时，模块一加载就报 ImportError。
    from daytona import Daytona, DaytonaConfig
    from langchain_daytona import DaytonaSandbox

    root = project_root or Path(__file__).resolve().parent.parent

    # 创建 Daytona 客户端：
    #   - api_key / target 等会自动从环境变量读，前提是入口已经 load_dotenv()；
    #   - auto_delete_interval=60 = 沙箱"持续停止"满 60 分钟后自动删除（TTL，单位：分钟）。
    client = Daytona(DaytonaConfig(auto_delete_interval=60))

    label = {"thread_id": thread_id}  # 纯 ASCII 标签，安全，用它来认领/复用本会话的沙箱

    # ========== get-or-create：先按 label 查，有就复用，没有就新建 ==========
    is_new = False
    sandbox = None
    try:
        # 本版 SDK（0.211.x）签名：client.list(query: ListSandboxesQuery | None)。
        # labels 过滤放在 ListSandboxesQuery 里；返回值是 generator，转成 list 取第一个。
        from daytona import ListSandboxesQuery

        existing = client.list(ListSandboxesQuery(labels=label))
        items = list(existing) if existing is not None else []
        if items:
            sandbox = items[0]
            logger.info("复用已存在的沙箱：thread_id={}", thread_id)
    except Exception as e:  # noqa: BLE001 查不到/接口差异 都当作"没有"，走新建分支
        logger.info("查询已有沙箱失败（将新建）：{}", e)

    if sandbox is None:
        sandbox = client.create()
        try:
            sandbox.set_labels(label)
        except Exception:  # noqa: BLE001 打 label 失败不影响本次使用
            pass
        is_new = True
        logger.info("新建沙箱：thread_id={}（TTL=60min）", thread_id)

    # 用沙箱对象包一层 LangChain 的后端适配器，它会自动给 Agent 提供文件工具 + execute。
    backend = DaytonaSandbox(sandbox=sandbox)

    if is_new:
        # 新建：必须把项目 seed 进去，seed 函数顺便会返回工作目录。
        workdir = seed_project_into_sandbox(backend, sandbox, root)
    else:
        # 复用：项目早就 seed 过了，只需重新问一下工作目录用于拼路径。
        try:
            workdir = (sandbox.get_workspace_root_dir() or "/home/daytona").rstrip("/")
        except Exception:
            workdir = "/home/daytona"

    return backend, sandbox, client, workdir
