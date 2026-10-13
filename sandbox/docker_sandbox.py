"""sandbox/docker_sandbox.py —— 自托管加固容器沙箱（实现 BaseSandbox 的四个成员）

依赖：本机装好 Docker。
导入路径（官方确认）：
    from deepagents.backends.sandbox import BaseSandbox
    from deepagents.backends.protocol import (
        ExecuteResponse, FileUploadResponse, FileDownloadResponse
    )
设计：容器在创建期就以加固参数起好并常驻（sleep infinity），本类的四个成员通过 docker 命令操作那个容器：
    - id              → 容器名
    - execute         → docker exec 跑命令
    - upload_files    → docker exec -i 管道把字节写进容器（已实测：docker cp 会被 --read-only 拦，
                         且 cp 落盘在 tmpfs 挂载层之下，容器内不可见，故弃用）
    - download_files  → docker exec cat 读出文件字节（与上传对称走 exec 通道）
注意：本类实现同步方法;BaseSandbox 会用 asyncio.to_thread 把它们包成异步(aexecute 等),
      所以 agent 走 ainvoke 时不阻塞事件循环。
"""
from __future__ import annotations

import os
import subprocess

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileUploadResponse,
    FileDownloadResponse,
)

from infra.logging import get_logger

logger = get_logger()


class DockerSandbox(BaseSandbox):
    """在一个加固 Docker 容器里执行命令、传输文件。容器生命周期由 docker_manager.py 管。"""

    def __init__(self, container_id: str, workdir: str = "/home/agent"):
        self.container_id = container_id
        self.workdir = workdir

    # ---------- 必须实现 1：唯一标识 ----------
    @property
    def id(self) -> str:
        return self.container_id

    # ---------- 必须实现 2：执行命令（同步）----------
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """在容器里跑一条 shell 命令，返回 ExecuteResponse（output + exit_code + truncated）。"""
        proc = subprocess.run(
            ["docker", "exec", "-w", self.workdir, self.container_id, "sh", "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # 合并 stderr 到 stdout
            timeout=timeout,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        truncated = len(text) > 8000
        return ExecuteResponse(
            output=text[-8000:],        # 截断尾部，避免巨量输出灌爆上下文
            exit_code=proc.returncode,
            truncated=truncated,
        )

    # ---------- 必须实现 3：上传文件（write_file/edit_file 底层依赖它）----------
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """把 [(沙箱内绝对路径, 字节内容), ...] 写进容器。逐个处理，支持部分成功（不抛异常）。

        不用 `docker cp`：加固容器根文件系统 --read-only 时，docker cp 的 rootfs 直通
        写入被 daemon 直接拒绝（container rootfs is marked read-only）；且 cp 落盘在
        挂载层之下，写进 tmpfs 工作区的文件容器内进程看不见。改走 exec 挂载通道：
        `docker exec -i ... cat > path`，字节从标准输入灌进 tmpfs（已实测二进制安全）。
        """
        results: list[FileUploadResponse] = []
        for path, content in files:
            try:
                parent = os.path.dirname(path)
                if parent:
                    subprocess.run(
                        ["docker", "exec", self.container_id, "mkdir", "-p", parent],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                    )
                proc = subprocess.run(
                    ["docker", "exec", "-i", self.container_id, "sh", "-c", f"cat > {path}"],
                    input=content,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
                if proc.returncode == 0:
                    results.append(FileUploadResponse(path=path))
                else:
                    err = proc.stdout.decode("utf-8", errors="replace") or "upload_failed"
                    results.append(FileUploadResponse(path=path, error=err))
            except Exception as e:  # noqa: BLE001 协议要求部分成功,不抛异常
                results.append(FileUploadResponse(path=path, error=str(e)))
        return results

    # ---------- 必须实现 4：下载文件 ----------
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从容器读出 paths 指定的文件字节。逐个处理，支持部分成功（不抛异常）。

        与上传对称走 exec 读通道：`docker exec ... cat path`，字节从标准输出原样带回，
        不落宿主临时文件。容器内无此文件时 docker 返回非零，记为 file_not_found。
        """
        results: list[FileDownloadResponse] = []
        for path in paths:
            try:
                proc = subprocess.run(
                    ["docker", "exec", self.container_id, "cat", path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
                if proc.returncode == 0:
                    results.append(FileDownloadResponse(path=path, content=proc.stdout))
                else:
                    results.append(
                        FileDownloadResponse(path=path, content=None, error="file_not_found")
                    )
            except Exception as e:  # noqa: BLE001
                results.append(FileDownloadResponse(path=path, content=None, error=str(e)))
        return results
