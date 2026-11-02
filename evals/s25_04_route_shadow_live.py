"""Real-provider, offline-only S25-04 shadow evaluation (no API or tools)."""
import asyncio
import json
import os
from pathlib import Path

from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from evals.s25_04_route_shadow import shadow_classify


# Expected label is a safety-grounded human annotation, not the model's prediction.
CASES = [
    ("task_write", "请创建待办：周五提交测试报告", False, "ordinary_tool"),
    ("memory_write", "记住我的测试编号是 QA-17", False, "ordinary_tool"),
    ("lookup", "请查询 MCP 的定义", False, "ordinary_tool"),
    ("calculate", "计算 (19+23)*4", False, "ordinary_tool"),
    ("list_tasks", "帮我查看尚未完成的待办", False, "ordinary_tool"),
    ("memory_read", "你还记得我的测试代号吗？", False, "ordinary_tool"),
    ("concept", "解释一下什么是执行计划", False, "ordinary_tool"),
    ("create_plan", "请建立两步可执行计划，先保存，不要执行", False, "plan_control"),
    ("execute_first", "请只执行已保存计划的第一步", True, "plan_control"),
    ("resume", "继续执行之前保存计划的剩余步骤", True, "plan_control"),
    ("revise", "修改已保存计划中的第二步", True, "plan_control"),
    ("cancel", "取消当前保存的执行计划", True, "plan_control"),
    ("create_with_existing", "新建一个三步执行计划并保存", True, "plan_control"),
    ("mixed", "先保存执行计划，再创建一个待办", False, "ambiguous"),
    ("mixed_memory", "先恢复已保存计划，再记住我的编号", True, "ambiguous"),
    ("mixed_lookup", "执行计划的第一步，同时查询 MCP 文档", True, "ambiguous"),
    ("unknown_prior", "继续之前的步骤", False, "ambiguous"),
    ("unknown_with_plan", "继续之前的步骤", True, "ambiguous"),
    ("anaphora", "就按刚才说的做", True, "ambiguous"),
    ("multiple_occurrences", "MCP 与 MCP 有什么不同？", False, "ordinary_tool"),
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
    repeats = int(os.getenv("S25_04_SHADOW_REPEATS", "3"))
    if not 1 <= repeats <= 20:
        raise ValueError("S25_04_SHADOW_REPEATS must be in [1, 20]")
    cases = []
    for name, request, saved, expected in CASES:
      for repeat in range(repeats):
          prediction = await shadow_classify(
              provider, settings.understanding_strong_model, request, saved)
          actual = prediction["label"]
          cases.append({"case": name, "repeat": repeat + 1, "expected": expected, "predicted": actual,
                        "reason": prediction["reason"], "matched": actual == expected})
    result = {"suite": "s25-04-route-shadow-v1", "scope": "offline-only",
              "provider": settings.model_provider,
              "model": settings.understanding_strong_model,
              "total": len(cases), "case_count": len(CASES), "repeats": repeats,
              "matched": sum(x["matched"] for x in cases),
              "plan_false_ordinary": sum(
                  x["expected"] != "ordinary_tool" and x["predicted"] == "ordinary_tool"
                  for x in cases),
              "ordinary_recall": sum(x["expected"] == "ordinary_tool" and x["predicted"] == "ordinary_tool" for x in cases),
              "ambiguous_count": sum(x["predicted"] == "ambiguous" for x in cases),
              "cases": cases}
    target = Path("evals/results/s25-04/route-shadow-v1.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Evidence:", target)


if __name__ == "__main__":
    asyncio.run(main())
