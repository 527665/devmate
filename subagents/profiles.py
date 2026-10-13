"""subagents/profiles.py —— DevMate 的五个专职子代理定义

工程化要点（全部来自官方 subagents 最佳实践）：
- description 动作导向，主 Agent 据此决定委派；
- system_prompt 写全（子代理不继承主 Agent 的 prompt）；
- tools 最小化（只给该角色需要的，提升聚焦与安全）；
- model 按角色经 llm_router 分档（省成本/保质量）；
- Reviewer 用 response_format 输出结构化结论（可编程消费）。
"""

from infra.llm_router import get_model_for_role
from tools.registry import get_tools
from subagents.schemas import ReviewResult


def build_subagents(workdir: str = "/home/daytona") -> list[dict]:
    """返回五个子代理的 dict 定义列表，供 create_deep_agent(subagents=...) 使用。

    workdir：沙箱工作目录（项目 seed 在它下面）。因为子代理【不继承】主 Agent 的
    system_prompt，所以会读写文件的子代理（coder/tester/researcher）必须各自写清
    "用 workdir 开头的绝对路径"，否则会写到根目录 / 而被沙箱拒绝（permission denied）。
    """

    # 会写/读文件的子代理统一加这段路径硬约束。
    path_rule = (
        f"\n\n【路径规则·必须遵守】项目在沙箱的 `{workdir}/` 下（`{workdir}/app/`、`{workdir}/tests/`）。"
        f"所有 read_file/write_file/edit_file/execute 都【必须】用以 `{workdir}/` 开头的绝对路径。"
        f"✅ 如 `{workdir}/app/pricing.py`、`{workdir}/tests/test_pricing.py`、`cd {workdir} && python -m pytest -q`。"
        f"❌ 禁止 `/test_pricing.py`、`test_pricing.py` 这类根路径/相对路径（会因权限被拒）。"
        f"不确定就先 `ls {workdir}`。"
    )

    planner = {
        "name": "planner",
        "description": "把一个开发 Issue 拆解成清晰、有序、可执行的步骤。当需要先规划再动手时使用。",
        "system_prompt": (
            "你是资深技术规划者。把给定的开发 Issue 拆成有序步骤：每步说明要改哪个文件、做什么、"
            "产出什么。只输出计划，不要写代码。计划要简洁，控制在 10 步以内；"
            "若 Issue 信息不足，先列出需要确认的问题而不是臆测。"
            f"涉及文件请用 `{workdir}/` 开头的绝对路径表述。"
        ),
        "tools": [],  # 纯规划，不需要工具（最小集）
        "model": get_model_for_role("planner"),  # strong 档（拆解要准）
    }

    researcher = {
        "name": "researcher",
        "description": "阅读仓库相关代码、检索外部资料，为改动提供事实依据。当需要先了解现状再动手时使用。",
        "system_prompt": (
            "你是代码调研员。用 read_file/grep 读相关代码，必要时用 web_search 查资料。"
            "只返回与任务相关的简洁发现（关键文件、现有实现、注意事项），不要贴大段原始代码。"
            "控制在 300 字以内。" + path_rule
        ),
        "tools": get_tools("search"),  # 搜索工具 + 继承的内置文件工具足够
        "model": get_model_for_role("researcher"),  # cheap 档（读+总结，省成本）
    }

    coder = {
        "name": "coder",
        "description": "按既定计划编写或修改代码。当计划已定、需要落地代码改动时使用。",
        "system_prompt": (
            "你是严谨的工程师。严格按计划改代码，遵循团队规范（类型注解齐全、金额用 Decimal、"
            "关键逻辑配 docstring、改动附带 pytest 测试）。改完用一两句话说明改了什么、为什么。" + path_rule
        ),
        "model": get_model_for_role("coder"),  # strong 档（写代码保质量）
        # tools 省略 = 继承主 Agent 工具（含文件工具、git 等）。
    }

    tester = {
        "name": "tester",
        "description": "为改动编写/补充测试，并在沙箱里运行 pytest 验证。当代码改完需要验证时使用。",
        "system_prompt": (
            f"你是测试工程师。为改动写 pytest 测试（放 `{workdir}/tests/`），然后用 execute 工具运行 "
            f"`cd {workdir} && python -m pytest -q` 验证。如果失败，分析原因并报告。"
            "最后只返回：测试是否通过、几条用例、失败的关键信息（若有）。不要贴完整测试输出。" + path_rule
        ),
        "model": get_model_for_role("tester"),  # standard 档
        # tester 需要 execute 工具——它由沙箱后端自动提供，故这里继承工具即可。
    }

    reviewer = {
        "name": "reviewer",
        "description": "审查代码改动质量并做安全自查，给出结构化结论。当改动完成、准备提 PR 前使用。",
        "system_prompt": (
            "你是代码审查者，也负责安全自查。检查：是否符合团队规范、有无明显 bug、"
            "有无硬编码密钥/危险操作/越权。给出结构化结论：是否通过、问题清单、安全发现。"
            f"只读代码（用 `{workdir}/` 开头的绝对路径 read_file/grep），不要修改任何文件。"
        ),
        "tools": get_tools("test"),  # 只读用途；下一行用 response_format 输出结构化结论
        "model": get_model_for_role("reviewer"),  # strong 档（审查要严）
        "response_format": ReviewResult,  # ← 结构化输出：parent 收到 JSON，可用代码判断 approved
    }

    return [planner, researcher, coder, tester, reviewer]
