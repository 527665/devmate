"""sandbox/docker_manager.py —— 加固容器的创建/seed/销毁

runtime 参数是隔离级别开关：runc(加固容器) / runsc(gVisor) / kata(microVM)。
换 runtime 就换隔离级别，DockerSandbox 代码不变。
这里用 asyncio.create_subprocess_exec 管容器生命周期(应用自己的异步逻辑)。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from infra.settings import get_settings
from infra.logging import get_logger
from obs.metrics import SANDBOX_CREATE_DURATION
from sandbox.docker_sandbox import DockerSandbox

logger = get_logger()


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return (proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", errors="replace"))


async def create_one_sandbox() -> DockerSandbox:
    """以加固参数起一个常驻容器，返回 DockerSandbox。"""
    start = time.perf_counter()
    try:
        s = get_settings()
        name = f"dm-sbx-{uuid.uuid4().hex[:12]}"
        workdir = s.sandbox_workdir

        # tmpfs 工作目录：可写 + 可执行 + 限大小 + 属主对齐非 root 用户(uid=1000)
        tmpfs_opt = f"{workdir}:rw,exec,size=512m,uid=1000"

        code, out = await _run([
            "docker", "run", "-d", "--name", name,
            "--runtime", s.sandbox_runtime,             # ← 换它即换隔离级别
            "--network", "none",                        # 断网
            "--cap-drop", "ALL",                        # 丢能力
            "--security-opt", "no-new-privileges",      # 禁提权
            "--read-only",                              # 只读根
            "--tmpfs", tmpfs_opt,                       # 唯一可写区
            "--tmpfs", "/tmp:rw,exec,size=128m,uid=1000",  # 额外给 /tmp 可写(部分工具需要)
            "--memory", s.sandbox_mem_limit,
            "--memory-swap", s.sandbox_mem_limit,
            "--pids-limit", str(s.sandbox_pids_limit),
            "--cpus", s.sandbox_cpus,
            "--user", "1000:1000",                      # 非 root
            "-w", workdir,
            s.sandbox_image, "sleep", "infinity",       # 常驻，等 docker exec
        ])
        if code != 0:
            raise RuntimeError(f"起沙箱容器失败：{out}")
        logger.info("加固容器已起：{}（runtime={}）", name, s.sandbox_runtime)
        return DockerSandbox(container_id=name, workdir=workdir)
    finally:
        # 埋点（12 章 3.4）：冷启动耗时。失败也记——docker 卡死/起不来恰恰最想看到耗时
        SANDBOX_CREATE_DURATION.observe(time.perf_counter() - start)


async def seed_project(
    sandbox: DockerSandbox,
    root: Path | None = None,
    subdirs: tuple[str, ...] = ("app", "tests", "skills"),
    extra_files: tuple[str, ...] = ("AGENTS.md",),
) -> None:
    """把宿主项目 seed 进容器工作目录（tar 管道走 docker exec -i）。

    注意：不 seed 的话沙箱是空的，coder 写不进文件、skills 报 path_not_found。

    ⚠ 不用 docker cp：加固容器 --read-only + 工作目录是 tmpfs 挂载时，
      cp 会把文件写进挂载层"地板下"的 rootfs，命令返回 0（静默假成功），
      容器内进程从 tmpfs 侧看却是空的——已实测复现。改走 exec 挂载通道：
      宿主 tar 打包 → stdin 管道 → 容器内 tar 解包，目录结构一次带走。
    """
    root = root or Path(__file__).resolve().parent.parent
    cid = sandbox.container_id
    workdir = sandbox.workdir

    # 收集要 seed 的相对路径（目录或文件，存在才收）
    targets = []
    for sub in subdirs:
        if (root / sub).exists():
            targets.append(sub)
    for f in extra_files:
        if (root / f).is_file():
            targets.append(f)
    if not targets:
        logger.warning("seed 目标为空（root={} 下没找到任何子目录/文件）", root)
        return

    # 一条命令完成：宿主 tar 打包这些路径 → 管道进容器 → 容器内 tar 解到 workdir
    # 用 shell 管道连接两个进程；路径都是本函数控制的相对名，无用户输入。
    names = " ".join(targets)
    cmd = (
        f'tar -C "{root}" -cf - {names} | '
        f'docker exec -i {cid} tar -xf - -C {workdir}'
    )
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    code = proc.returncode if proc.returncode is not None else -1
    if code != 0:
        raise RuntimeError(f"seed 失败（code={code}）：{out.decode('utf-8', errors='replace')}")

    # seed 进来的文件属主可能是 root，统一改成 agent(1000)，否则非 root 进程改不动
    await _run(["docker", "exec", "-u", "0", cid, "chown", "-R", "1000:1000", workdir])
    logger.info("项目已 seed 进容器 {}（{}）", cid, names)


async def destroy_sandbox(sandbox: DockerSandbox) -> None:
    """销毁容器（用完即弃）。tmpfs 工作目录随容器一起消失，不残留。"""
    await _run(["docker", "rm", "-f", sandbox.container_id])
    logger.info("容器已销毁：{}", sandbox.container_id)


async def create_readonly_sandbox(source_sandbox: DockerSandbox) -> DockerSandbox:
    """给 reviewer 起一个只读容器：把 source_sandbox 的工作目录内容复制进来后设为不可写。

    实现：① 起一个新加固容器（工作目录仍是 tmpfs，此刻可写）；
         ② 从源容器把代码 cp 出来（读方向，不受 --read-only 限制）；
         ③ 经宿主中转搬进新容器（★适配 1：不用 docker cp 拷入——那是实测过的
            tmpfs 静默假成功坑，见第 12 节 bug 1/2，改走 exec 的 tar 管道）；
         ④ chown root:root + chmod -R a-w（★适配 2：不 chown 的话文件属主是 1000，
            agent 自己一条 chmod +w 就把写位改回来了，只读形同虚设；
            属主改成 root 后，非 root 的 agent 无权改权限位）。
    这样 reviewer 即使用 execute 也改不了代码——靠文件系统权限,不靠 prompt。
    """
    import tempfile
    s = get_settings()
    name = f"dm-ro-{uuid.uuid4().hex[:12]}"
    workdir = s.sandbox_workdir
    code, out = await _run([
        "docker", "run", "-d", "--name", name,
        "--runtime", s.sandbox_runtime,
        "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--read-only",
        "--tmpfs", f"{workdir}:rw,exec,size=512m,uid=1000",
        "--memory", s.sandbox_mem_limit, "--memory-swap", s.sandbox_mem_limit,
        "--pids-limit", str(s.sandbox_pids_limit), "--cpus", s.sandbox_cpus,
        "--user", "1000:1000", "-w", workdir,
        s.sandbox_image, "sleep", "infinity",
    ])
    if code != 0:
        raise RuntimeError(f"起只读沙箱失败：{out}")
    ro = DockerSandbox(container_id=name, workdir=workdir)
    # 把源容器代码搬进来(经宿主中转),然后把工作目录设为不可写
    with tempfile.TemporaryDirectory() as tmpd:
        await _run(["docker", "cp", f"{source_sandbox.container_id}:{workdir}/.", tmpd])
        # ★适配 1：docker cp 拷入 → tar 管道走 exec 通道（与 seed_project 同一招）
        proc = await asyncio.create_subprocess_shell(
            f'tar -C "{tmpd}" -cf - . | docker exec -i {name} tar -xf - -C {workdir}',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        tout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"拷入只读沙箱失败（code={proc.returncode}）："
                f"{tout.decode('utf-8', errors='replace')}"
            )
    # 去掉写位（用 root 改,因为非 root 改不动自己没权限的位）
    # ★适配 2：chmod 前先把属主改成 root，否则 1000 属主随时能 chmod +w 自救
    await _run(["docker", "exec", "-u", "0", name, "chown", "-R", "root:root", workdir])
    await _run(["docker", "exec", "-u", "0", name, "chmod", "-R", "a-w", workdir])
    logger.info("只读 reviewer 沙箱已起：{}", name)
    return ro
