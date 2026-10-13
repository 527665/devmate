"""基于 loguru 的 DevMate 统一日志配置。"""

import sys
from pathlib import Path

from loguru import logger

from infra.settings import get_settings


_CONFIGURED = False
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def setup_logging() -> None:
    """初始化全局日志；重复调用时只配置一次。"""

    global _CONFIGURED
    if _CONFIGURED:
        return

    level = get_settings().log_level.upper()

    # 移除 loguru 默认处理器，避免重复输出。
    logger.remove()

    # Windows 中文终端可能使用 GBK，无法输出验证脚本里的 ✅ 等字符。
    # 能调整编码时统一使用 UTF-8；重定向到不支持 reconfigure 的对象时保持原样。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    # 控制台日志：适合本地开发和实时观察程序运行状态。
    logger.add(
        sys.stdout,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <7}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        backtrace=True,
        diagnose=False,
        colorize=True,
    )

    # 文件日志：单个文件超过 10 MB 时轮转，旧日志保留 7 天并压缩。
    log_dir = _PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "devmate_{time:YYYY-MM-DD}.log",
        level=level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    _CONFIGURED = True


def get_logger():
    """返回业务代码统一使用的 loguru 全局 logger。"""

    setup_logging()
    return logger
