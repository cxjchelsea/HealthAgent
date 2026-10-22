"""S25-01 REAL-provider goal-understanding benchmark; never a FakeProvider test.

Run only against an explicitly configured reachable model endpoint:
  RUN_S25_01_LIVE=1 MODEL_PROVIDER=ollama_proxy MODEL_BASE_URL=http://127.0.0.1:11434 \
    UNDERSTANDING_LIGHT_MODEL=qwen3:4b UNDERSTANDING_STRONG_MODEL=qwen3:14b \
    python -m evals.s25_01_live --output s25_01_live.json

This isolates understanding accuracy, routing and latency; it does NOT certify
whole-Agent task execution, hardware-agnostic speedups or final acceptance.
"""
import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from agent.config import Settings
from agent.understanding import (
    GoalSpec, UnderstandingContext, UnderstandingService, HTTPUnderstandingModel,
    UnderstandingError,
)

# Scenarios use paraphrases and distinct interaction types. No model-generated
# raw personal messages are persisted in benchmark reports.
CASES = (
    {"id": "new_goal", "message": "把明天要做的事整理出一个简短草案，不要替我保存",
     "expected": ("new_goal",), "constraint": "save"},
    {"id": "new_goal_variant", "message": "列个明日安排供我参考就行，先别落库",
     "expected": ("new_goal",), "constraint": "save"},
    {"id": "multi_goal", "message": "先查看已有待办，再整理成三项建议，只要草稿",
     "expected": ("new_goal",), "constraint": "three"},
    {"id": "revise", "message": "第二项挪到周五，其他内容保持原样",
     "expected": ("revise",), "has_context": True},
    {"id": "continue", "message": "照前面的安排继续完成未处理事项",
     "expected": ("continue",), "has_context": True},
    {"id": "add", "message": "原来的三项保留，再补一条周末买菜",
     "expected": ("add",), "has_context": True},
    {"id": "missing_referent", "message": "第二个改一下",
     "expected": ("revise", "clarify_response"), "clarify": True},
    {"id": "cancel", "message": "先停止刚才那个计划，别继续执行",
     "expected": ("cancel_request",), "has_context": True},
    {"id": "clarification", "message": "帮我处理一下那个",
     "expected": ("new_goal", "continue", "revise", "chat"), "clarify": True},
    {"id": "cross_language", "message": "Please draft tomorrow's top three tasks. Don't save anything.",
     "expected": ("new_goal",), "constraint": "save"},
)

def context_for(case):
    previous = (GoalSpec(
        objective="安排明天三项任务",
        subgoals=["审查资料", "完成计算", "整理总结"],
    ) if case.get("has_context") else None)
    return UnderstandingContext(
        current_message=case["message"],
        active_goal=previous,
        current_plan_summary=[{"id": 2, "goal": "完成计算", "status": "pending",
                               "revision": 1}] if previous else [],
        recent_completed_messages=[
            {"role": "user", "content": "请安排明天三项任务"},
            {"role": "assistant", "content": "我会先给草案，不执行写入"},
        ] if previous else [],
    )

def meets_expectations(case, result):
    value = result["result"]
    correct_kind = value.change.kind in case["expected"]
    correct_clarify = (not case.get("clarify") or value.clarification.required)
    constraint_text = " ".join(
        f"{x.name} {x.value}" for x in (value.goal.constraints if value.goal else [])
    ).lower()
    constraint_marker = case.get("constraint")
    if constraint_marker == "save":
        correct_constraint = any(word in constraint_text for word in
                                 ("不保存", "不要保存", "不写入", "不落库", "do not save", "don't save", "no save"))
    elif constraint_marker == "three":
        correct_constraint = any(word in constraint_text for word in ("三", "3", "three"))
    else:
        correct_constraint = True
    return {"kind": correct_kind, "clarification": correct_clarify,
            "constraint": correct_constraint}

async def run_strategy(settings, name, service, case):
    started = time.monotonic()
    try:
        outcome = await service.understand(context_for(case), use_strong=(name == "strong"))
        scores = meets_expectations(case, outcome)
        return {"case_id": case["id"], "strategy": name, "valid": True,
                "passed": all(scores.values()), "checks": scores,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "attempts": outcome["attempts"], "model_role": outcome["model_role"],
                "escalated": outcome["escalated"]}
    except UnderstandingError as error:
        return {"case_id": case["id"], "strategy": name, "valid": False,
                "passed": False, "error": error.code,
                "duration_ms": int((time.monotonic() - started) * 1000)}

def summary(rows):
    output = {}
    for name in ("light_then_strong", "strong"):
        subset = [x for x in rows if x["strategy"] == name]
        durations = sorted(x["duration_ms"] for x in subset)
        output[name] = {
            "total": len(subset), "valid": sum(x["valid"] for x in subset),
            "passed": sum(x["passed"] for x in subset),
            "pass_rate": sum(x["passed"] for x in subset) / len(subset),
            "p50_ms": statistics.median(durations),
            "p95_ms": durations[min(len(durations)-1, max(0, int(len(durations)*.95 + .999)-1))],
            "escalations": sum(bool(x.get("escalated")) for x in subset),
            "model_attempts": sum(len(x.get("attempts", [])) for x in subset),
        }
    return output

async def main(output):
    settings = Settings()
    if not settings.run_s25_01_live:
        raise SystemExit("Set RUN_S25_01_LIVE=1 explicitly; no fake score is generated.")
    if settings.model_provider not in {"ollama_proxy", "openai_compatible"}:
        raise SystemExit("A real model provider is required.")
    provider = HTTPUnderstandingModel(
        settings.model_provider, settings.model_base_url,
        settings.model_api_key, settings.model_timeout_seconds,
        settings.model_max_output_tokens)
    if settings.understanding_light_model == settings.understanding_strong_model:
        raise SystemExit("Light/strong models must differ for a tier comparison.")
    service = UnderstandingService(provider, settings.understanding_light_model,
                                   settings.understanding_strong_model)
    rows = []
    for case in CASES:
        for name in ("light_then_strong", "strong"):
            rows.append(await run_strategy(settings, name, service, case))
    report = {
        "evidence": "real_provider_requests_not_mock",
        "scope": "isolated_understanding_only_not_full_Agent_E2E",
        "provider": settings.model_provider,
        "light_model": settings.understanding_light_model,
        "strong_model": settings.understanding_strong_model,
        "case_count": len(CASES),
        "summary": summary(rows),
        "rows": rows,
        "limitations": [
            "Heuristic scoring for named scenarios; requires manual semantic review.",
            "Does not measure direct AgentLoop baseline or total Agent tool latency.",
            "Cold model loading, hardware and concurrent load can change latency.",
            "Do not change production defaults or close S25-01 on this report alone.",
        ],
    }
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("Wrote", output)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="s25_01_live.json")
    args = parser.parse_args()
    asyncio.run(main(args.output))
