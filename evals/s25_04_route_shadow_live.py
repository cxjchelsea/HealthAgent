"""Real-provider, offline-only S25-04 shadow evaluation (no API or tools)."""
import asyncio
import json
import os
from pathlib import Path

from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from evals.s25_04_route_shadow import shadow_classify


CASES = [
    ("task_write", "请创建待办：周五提交测试报告", False, "ordinary_tool"),
    ("memory_write", "记住我的测试编号是 QA-17", False, "ordinary_tool"),
    ("lookup", "请查询 MCP 的定义", False, "ordinary_tool"),
    ("calculate", "计算 (19+23)*4", False, "ordinary_tool"),
    ("create_plan", "请建立两步可执行计划，先保存，不要执行", False, "plan_control"),
    ("execute_first", "请只执行已保存计划的第一步", True, "plan_control"),
    ("resume", "继续执行之前保存计划的剩余步骤", True, "plan_control"),
    ("revise", "修改已保存计划中的第二步", True, "plan_control"),
    ("cancel", "取消当前保存的执行计划", True, "plan_control"),
    ("mixed", "先保存执行计划，再创建一个待办", False, "ambiguous"),
    ("unknown_prior", "继续之前的步骤", False, "ambiguous"),
    ("concept", "解释一下什么是执行计划", False, "ambiguous"),
]


async def main():
    if os.getenv("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("RUN_S25_04_LIVE=1 required")
    settings = Settings()
    if settings.model_provider == "fake":
        raise RuntimeError("Real model required")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    cases = []
    for name, request, saved, expected in CASES:
        prediction = await shadow_classify(
            provider, settings.understanding_strong_model, request, saved)
        actual = prediction["label"]
        cases.append({"case": name, "expected": expected, "predicted": actual,
                      "reason": prediction["reason"], "matched": actual == expected})
    result = {"suite": "s25-04-route-shadow-v1", "scope": "offline-only",
              "provider": settings.model_provider,
              "model": settings.understanding_strong_model,
              "total": len(cases), "matched": sum(x["matched"] for x in cases),
              "plan_false_ordinary": sum(
                  x["expected"] == "plan_control" and x["predicted"] == "ordinary_tool"
                  for x in cases), "cases": cases}
    target = Path("evals/results/s25-04/route-shadow-v1.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Evidence:", target)


if __name__ == "__main__":
    asyncio.run(main())
