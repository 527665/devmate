---
name: unit-test
description: 当需要为 DevMate 的函数、中间件或接口编写、补充单元测试时使用本规范（pytest 风格、命名、覆盖要点）。
---

# 单元测试编写规范

## 框架与结构
- 用 pytest；测试文件命名 test_<module>.py，放在 tests/ 下。
- 一个测试只验证一件事，采用 Arrange-Act-Assert 三段式。

## 命名
- 测试函数名描述场景，如 test_build_agent_with_custom_identity。

## 覆盖要点
- 正常路径 + 边界（空输入、缺省配置）+ 异常输入。
- 用 pytest.raises 验证错误路径。
