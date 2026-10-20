"""S2-D: one-shot Ollama-compatible Tool Decision compatibility matrix.

Run locally with RUN_S2D_LIVE=1. No inference results, prompts, keys or
reasoning text are recorded. Each case calls the same NativeToolDecision
adapter as the official conversation API, without faking tool decisions.
"""
import asyncio
import json
import os
import time
from pathlib import Path

from agent.config import Settings
from agent.decision import NativeToolDecision, ToolDecisionError
from agent.providers import ModelUnavailable
from agent.tools import TOOL_SCHEMAS
from agent.loop import PROMPT
from agent.skills import load_instructions, SKILL_VERSION

CASES = (
    ("plain_text_no_tools", "请用一句话回答：2加2等于多少？", ()),
    ("calculate_only", "请调用 calculate 工具计算 8*8。", ("calculate",)),
    ("plan_and_calculate", "请先用 propose_plan 建立计算8*8的计划，然后调用 calculate。",
     ("propose_plan", "calculate", "revise_plan")),
    ("recovery_goal", "请使用 propose_plan 制定两个带 expected_tool 和 expected_arguments 的步骤。"
     "第一步计算 8/0，用 calculate 观察真实除零错误；第二步在收到失败后调用"
     "revise_plan 只保留待执行任务，将错误计算替换为 calculate(expression='8*8')，"
     "最终核验结果 64。如果第一步失败，不要假称成功。",
     ("propose_plan", "calculate", "revise_plan")),
)


# A controlled comparison: identical system prompt, tools, and provider settings.
# Only the user goal changes between these three requests.
FIXED_GOALS = (
    ("fixed_simple", "请先用 propose_plan 制定计算8*8的计划，再调用 calculate。"),
    ("fixed_failure", "请先用 propose_plan 制定计划，先用 calculate 计算8/0并观察失败，再修订计划计算8*8。"),
    ("fixed_original", CASES[-1][1]),
)

def build_cases():
    for name, goal, allowed in CASES:
        system = PROMPT + "\nSelected skill instructions (" + SKILL_VERSION + " ):\n" + (
            load_instructions(goal, strategy="dynamic", max_chars=2000))
        yield name, goal, allowed, system, "incremental"
    fixed_system = PROMPT + "\nSelected skill instructions (" + SKILL_VERSION + " ):\n" + (
        load_instructions(CASES[-1][1], strategy="dynamic", max_chars=2000))
    for name, goal in FIXED_GOALS:
        yield name, goal, ("propose_plan", "calculate", "revise_plan"), fixed_system, "fixed_request"

async def evaluate():
    if os.getenv("RUN_S2D_LIVE") != "1":
        raise RuntimeError("Set RUN_S2D_LIVE=1 before accessing the real model")
    settings = Settings()
    adapter = NativeToolDecision(settings)
    results = []
    for name, goal, allowed, instruction, group in build_cases():
        tools = [schema for schema in TOOL_SCHEMAS if schema["function"]["name"] in allowed]
        started = time.monotonic()
        case = {"case_id": name, "comparison_group": group, "tools_count": len(tools),
                "prompt_chars": len(instruction), "goal_chars": len(goal)}
        try:
            decision = await adapter.decide([
                {"role": "system", "content": instruction},
                {"role": "user", "content": goal}], tools)
            case.update({"status": "decision", "has_content": bool(decision.content.strip()),
                         "tool_call_count": len(decision.tool_calls),
                         "tool_names": [c["name"] for c in decision.tool_calls]})
        except ToolDecisionError as exc:
            case.update({"status": "provider_error", "reason_code": exc.code,
                         "response_shape": exc.metadata})
        except ModelUnavailable:
            case.update({"status": "provider_error", "reason_code": "model_unavailable"})
        case["duration_ms"] = round((time.monotonic() - started) * 1000)
        results.append(case)
    return {"suite": "s2d-provider-compatibility-v2",
            "mode": "REAL_PROVIDER", "provider": settings.model_provider,
            "model": settings.model_name, "cases": results,
            "limitations": ["One request per case; no stability inference",
                            "Tool calls are inspected but NEVER executed",
                            "Fixed-request cases share identical system prompt and tool schemas",
                            "No raw responses, thinking text, credentials or prompts are stored"]}


def main():
    report = asyncio.run(evaluate())
    path = Path("evals/results/s2d/qwen-provider-compatibility-v2.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for case in report["cases"]:
        print(case["case_id"], case["status"],
              case.get("reason_code", ""), "ms=", case["duration_ms"])
    print("Evidence:", path)


if __name__ == "__main__":
    main()
