"""eval/code_cases.py —— 带隐藏测试的编码用例（客观评分）

每题 = issue + 可运行的隐藏测试。agent 在沙箱里实现，
我们往沙箱塞入自己的 test_code（覆盖 agent 可能写的同名测试），跑 pytest 数通过率。
score 只有 0/1 —— 能运行验证的任务不需要模糊打分（客观真值层）。
"""

CODE_EVAL_CASES = [
    {
        "id": "exec-01",
        "category": "exec",
        "issue": "在 /home/agent/app/calc.py 实现 calc_total(items)，"
                 "items 形如 [{'price':'10.00','qty':2}]，金额用 Decimal，返回总价字符串。",
        "target_file": "app/calc.py",                # 相对 workdir，用于静态检查取回
        "test_file": "/home/agent/tests/test_calc.py",
        "test_code": (
            "from decimal import Decimal\n"
            "from app.calc import calc_total\n"
            "def test_basic():\n"
            "    assert calc_total([{'price':'10.00','qty':2}]) == '20.00'\n"
            "def test_multi():\n"
            "    assert calc_total([{'price':'1.50','qty':3},{'price':'2.00','qty':1}]) == '6.50'\n"
        ),
    },
    # ... 更多编码用例，每条都带可运行的隐藏测试 ...
]
