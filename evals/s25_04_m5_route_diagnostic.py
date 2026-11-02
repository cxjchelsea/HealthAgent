"""Read-only offline M5 route comparison. No tools or API side effects."""
import asyncio
import json
import os
from pathlib import Path

from agent.config import Settings
from agent.m5_unified_route import propose_new_plan_route, SavedPlanRouteRefused
from agent.understanding import HTTPUnderstandingModel, UnderstandingError

CASES = [
    ("calculation", "请计算 (19+23)*4", "other"),
    ("tasks", "请创建一个任务：周五提交测试报告", "other"),
    ("memory", "记住我的测试代号是 QA-17", "other"),
    ("lookup", "请查询 MCP 中 RAG 的定义", "other"),
    ("list", "查询当前会话中已有的任务", "other"),
    ("explanation", "解释一下数学中的加法", "other"),
    ("new_plan", "请创建一个两步可执行计划：第一步计算 3+4，第二步计算 8+9。只保存，不执行。", "plan_only"),
    ("missing_plan", "继续执行之前保存计划的剩余步骤", "resume"),
]

async def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("RUN_S25_04_LIVE=1 required")
    s = Settings()
    if s.model_provider == "fake":
        raise RuntimeError("Real provider required")
    provider = HTTPUnderstandingModel(s.model_provider, s.model_base_url,
                                     s.model_api_key, s.model_timeout_seconds,
                                     s.model_max_output_tokens)
    repeats = int(os.getenv("S25_04_M5_ROUTE_REPEATS", "3"))
    if not 1 <= repeats <= 10:
        raise ValueError("invalid repeats")
    rows = []
    for case, message, expected in CASES:
        for i in range(repeats):
            try:
                result = await propose_new_plan_route(provider, s.understanding_strong_model, message)
                actual, code = result.intent, None
            except SavedPlanRouteRefused as e:
                actual, code = "refused", str(e)
            except UnderstandingError as e:
                actual, code = "unavailable", e.code
            rows.append(dict(case=case, repeat=i+1, expected=expected,
                             actual=actual, refusal_code=code, matched=actual==expected))
    out = dict(suite="s25-04-m5-route-diagnostic-v1", total=len(rows),
               matched=sum(row["matched"] for row in rows),
               model=s.understanding_strong_model, cases=rows)
    path = Path("evals/results/s25-04/m5-route-diagnostic-v1.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
