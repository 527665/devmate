"""eval/judge.py —— LLM-as-judge（开放题兜底）

让模型按 criteria 给被测回答打分（0-10）。要点：裁判 prompt 清晰、要求 JSON 输出。
注：裁判模型从 infra/llm_router 的 get_model 取；若你的 get_model 支持 temperature，传 0 降波动，
    否则用默认即可，但要多跑几轮取均值压方差——裁判本身有噪声。

★项目实况（读码确认）：infra/llm_router.py 的 _build 已统一传 temperature=0，
  裁判模型天然低波动，上述 temperature=0 的条件在本项目已满足；方差处理仍靠重复跑取均值控制。
"""
import json

from infra.llm_router import get_model

JUDGE_PROMPT = """你是一个严格的 AI 回答质量评审。请根据【评分标准】给【被测回答】打分。

【用户问题】
{question}

【评分标准】
{criteria}

【被测回答】
{answer}

打分要求：
- 0-10 分（10 分为完全符合标准、准确且有用）；
- 重点看是否满足标准的实质，而非用词是否一致（意思对即可给高分）；
- 回答错误、答非所问、该拒绝却没拒绝，给低分。

只输出一个 JSON：{{"score": <0-10整数>, "reason": "<简短理由>"}}
不要输出 JSON 以外的任何内容。"""


async def judge_answer(question: str, criteria: str, answer: str) -> dict:
    """用裁判模型给一个回答打分，返回 {"score": 0-1 浮点, "reason": str}。"""
    judge_model = get_model("standard")     # 裁判用中档模型；如支持 temperature 可传 0
    prompt = JUDGE_PROMPT.format(question=question, criteria=criteria, answer=answer)
    resp = await judge_model.ainvoke(prompt)
    text = resp.content if hasattr(resp, "content") else str(resp)
    try:
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        return {"score": max(0.0, min(10.0, float(data.get("score", 0)))) / 10.0,
                "reason": data.get("reason", "")}
    except Exception:  # noqa: BLE001 裁判输出解析失败给 0 分
        return {"score": 0.0, "reason": f"裁判输出解析失败: {text[:80]}"}
