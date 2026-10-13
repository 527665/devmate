"""各章试跑脚本共用的打印工具。"""

import sys
from typing import Any


# 任务状态 -> 图标，让任务清单更容易阅读。
_STATUS_ICON = {
    "completed": "✅",
    "in_progress": "🔄",
    "pending": "⬜",
}


def _prepare_stdout() -> None:
    """尽量使用 UTF-8，兼容 Windows 中文终端和重定向输出。"""

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _message_content(message: Any) -> Any:
    """兼容 LangChain 消息对象和字典形式的消息。"""

    if isinstance(message, dict):
        return message.get("content", message)
    return getattr(message, "content", message)


def print_reply(result: dict[str, Any]) -> None:
    """打印 Agent 最后一条消息。"""

    _prepare_stdout()
    print("\n=== DevMate 最终回复 ===")
    messages = result.get("messages") or []
    print(_message_content(messages[-1]) if messages else "（没有 messages）")


def print_files(result: dict[str, Any]) -> None:
    """打印 Agent 本次写入的文件。"""

    _prepare_stdout()
    print("\n=== DevMate 写入的文件 ===")
    files = result.get("files") or {}
    if not files:
        print("（本次没有写文件）")
        return

    for name, data in files.items():
        print(f"\n--- {name} ---")
        # StateBackend 中的文件值可能是字符串，也可能是带 content 的字典。
        if isinstance(data, dict):
            print(data.get("content", data))
        else:
            print(getattr(data, "content", data))


def print_todos(result: dict[str, Any]) -> None:
    """打印任务清单；没有清单时给出明确提示。"""

    _prepare_stdout()
    print("\n=== DevMate 的任务清单 ===")
    todos = result.get("todos") or []
    if not todos:
        print("（本次没列计划：任务较简单，DevMate 没调用 write_todos —— 这是正常的）")
        return

    for index, todo in enumerate(todos, 1):
        # 兼容 todo 是字典、Pydantic 对象或普通对象的情况。
        if isinstance(todo, dict):
            content = todo.get("content", str(todo))
            status = todo.get("status", "pending")
        else:
            content = getattr(todo, "content", str(todo))
            status = getattr(todo, "status", "pending")

        icon = _STATUS_ICON.get(status, "•")
        print(f"  {index}. {icon} [{status}] {content}")


def print_all(result: dict[str, Any]) -> None:
    """依次打印回复、文件和任务清单。"""

    print_reply(result)
    print_files(result)
    print_todos(result)
