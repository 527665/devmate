# -*- coding: utf-8 -*-
"""pytest 根配置：让仓库根目录进 sys.path。

为什么需要这个文件（实测差异）：
  tests/test_demo.py 写 `from app.demo import add`，tests/test_orders.py 写
  `from gateway.main import app`，这要求「仓库根」在 sys.path 上。
  `uv run pytest` 走 venv 里的 pytest 入口脚本，sys.path[0] 是 venv 的 bin 目录，
  不是仓库根 -> ModuleNotFoundError: No module named 'app'。
  `python -m pytest` 会把当前目录塞进 sys.path，所以能过。
  pytest 的 rootdir 机制：仓库根存在 conftest.py 时，该目录会被插入 sys.path，
  于是两种调用方式都能跑。CI 里用的是 `uv run pytest`，所以需要它。
"""
