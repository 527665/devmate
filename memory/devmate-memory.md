# DevMate · 团队约定（常驻记忆）

## 项目

- 技术栈：Python 3.13 + FastAPI + DeepAgents/LangChain/LangGraph；包管理用 uv。
- 目录：Agent 相关在 agent/，基础设施在 infra/，中间件在 middleware/，
  试跑脚本在 scripts/，技能手册在 skills/，测试放 tests/。

## 必须遵守（铁律）

- 金额一律用 Decimal 计算，**禁止用 float 做货币运算**。
- 所有公开函数带类型注解和 docstring。
- 任何代码改动都必须附带对应的 pytest 测试。

## 风格

- 内部命名用 snake_case；对外 API 字段用 camelCase（由 Pydantic alias 处理）。
- 提交信息用祈使句，一行概述 + 可选正文。