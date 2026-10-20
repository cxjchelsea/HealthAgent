"""Real Qwen All/Dynamic comparison with evidence-based arithmetic diagnostics.

Live calls are opt-in. No raw prompts, answers, credentials or other tool payloads
are persisted. Arithmetic evidence contains only bounded expressions and numbers.
"""
import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path

from agent.config import Settings
from agent.decision import NativeToolDecision
from agent.loop import AgentLoop
from agent.mcp_stdio_client import StdioMCPClient

CASES = (
    ("arithmetic", "请使用工具计算(18+24)*3，告诉我结果。", "calculate", "126"),
    ("reference", "用参考工具查询 RAG 的定义，再给出结果。", "mcp_reference_lookup", "retriev"),
    ("clock", "请调用工具读取现在 UTC 时间。", "utc_time", "utc"),
    ("implicit_reference", "请从参考词条获取 rag 的含义。", "mcp_reference_lookup", "retriev"),
    ("math_explicit", "请先把18与24相加，再把这个和乘3，调用计算工具。", "calculate", "126"),
    ("math_precedence", "按通常运算优先级计算 18+24*3，请使用计算工具。", "calculate", "90"),
    ("math_ambiguous", "18加24乘3是多少？若表达不明确请先澄清，不要擅自决定括号。", None, None),
    ("multi_tool", "请先查询 RAG 的术语解释，再算一下 8*8。", None, None),
    ("unknown_reference", "请通过参考工具查询不存在的术语 xyznothing。", "mcp_reference_lookup", None),
    ("unsupported", "请执行 shell 命令删除文件。", None, None),
)


def arithmetic_evidence(audit):
    evidence = []
    for item in audit:
        if item.get("name") != "calculate":
            continue
        args = item.get("arguments")
        observation = item.get("observation") or {}
        expression = args.get("expression") if isinstance(args, dict) else None
        if not isinstance(expression, str) or len(expression) > 160:
            expression = None
        value = observation.get("value")
        if type(value) not in (int, float):
            value = None
        evidence.append({"expression": expression, "value": value,
                         "ok": observation.get("ok") is True,
                         "error_code": observation.get("error")})
    return evidence


def grade(case_id, outcome):
    audit = outcome.get("audit", [])
    attempted = [item["name"] for item in audit]
    checks = {"completed": outcome["status"] == "completed"}
    expected = next((c for c in CASES if c[0] == case_id), None)
    if expected is None:
        raise ValueError("unknown case")
    _, _, required_tool, answer_marker = expected
    if required_tool:
        checks["required_tool_called"] = required_tool in attempted
        if case_id == "unknown_reference":
            checks["expected_failure_observed"] = any(
                x["name"] == required_tool and x["observation"].get("ok") is False
                for x in audit)
        else:
            checks["required_tool_succeeded"] = any(
                x["name"] == required_tool and x["observation"].get("ok") is True
                for x in audit)
    if case_id in {"arithmetic", "math_explicit", "math_precedence"}:
        expected_value = 90 if case_id == "math_precedence" else 126
        checks["correct_tool_result"] = any(
            x["value"] == expected_value and x["ok"] for x in arithmetic_evidence(audit))
    if answer_marker:
        checks["answer_contains_marker"] = answer_marker in outcome["answer"].casefold()
    if case_id == "multi_tool":
        checks["both_tools_called"] = {"calculate", "mcp_reference_lookup"}.issubset(set(attempted))
        checks["both_tools_succeeded"] = all(
            any(x["name"] == name and x["observation"].get("ok") for x in audit)
            for name in ("calculate", "mcp_reference_lookup"))
    if case_id == "math_ambiguous":
        # This checks clarification evidence, not merely a computed number.
        # Human review is still required for nuanced clarification language.
        checks["no_unapproved_calculation"] = "calculate" not in attempted
        checks["clarification_requested"] = any(
            term in outcome["answer"].casefold()
            for term in ("括号", "先算", "歧义", "哪种", "确认", "明确", "clarif", "mean"))
    if case_id == "unsupported":
        checks["no_tool_called"] = not attempted
    return checks


async def measure(strategy, case, settings):
    case_id, goal, _, _ = case
    started = time.monotonic()
    try:
        outcome = await AgentLoop(
            NativeToolDecision(settings), mcp_client=StdioMCPClient(),
            skill_strategy=strategy, max_steps=6
        ).run(goal, include_evidence=True)
        audit = outcome.get("audit", [])
        checks = grade(case_id, outcome)
        return {"id": case_id, "strategy": strategy, "passed": all(checks.values()),
                "checks": checks, "status": outcome["status"],
                "tools": [item["name"] for item in audit],
                "arithmetic": arithmetic_evidence(audit),
                "tool_success": sum(bool(x["observation"].get("ok")) for x in audit),
                "steps": outcome.get("steps"),
                "latency_ms": round((time.monotonic()-started)*1000),
                "error": None}
    except Exception as exc:
        return {"id": case_id, "strategy": strategy, "passed": False,
                "checks": {}, "status": "exception", "tools": [],
                "arithmetic": [], "tool_success": 0, "steps": None,
                "latency_ms": round((time.monotonic()-started)*1000),
                "error": type(exc).__name__}


async def evaluate(repeats=2, cases=CASES):
    if os.getenv("RUN_S2C_LIVE") != "1":
        raise RuntimeError("Set RUN_S2C_LIVE=1 to authorize provider calls")
    settings = Settings()
    if settings.model_provider not in ("ollama_proxy", "openai_compatible"):
        raise RuntimeError("Configure a real provider")
    rows = []
    for repeat in range(repeats):
        for case in cases:
            order = ("all", "dynamic") if repeat % 2 else ("dynamic", "all")
            for strategy in order:
                rows.append(await measure(strategy, case, settings))
    summary = {}
    for strategy in ("all", "dynamic"):
        group = [r for r in rows if r["strategy"] == strategy]
        summary[strategy] = {
            "attempts": len(group), "passed": sum(r["passed"] for r in group),
            "task_success_rate": sum(r["passed"] for r in group)/len(group),
            "median_latency_ms": statistics.median(r["latency_ms"] for r in group),
            "tool_call_count": sum(len(r["tools"]) for r in group),
        }
    return {"mode": "REAL_PROVIDER", "model": settings.model_name,
            "provider": settings.model_provider, "repeats": repeats,
            "results": rows, "comparison": summary,
            "limitations": ["Small nonrandomized sample; no confidence intervals",
                            "Heuristic answer/clarification grading requires human review",
                            "Arithmetic evidence is restricted to expressions and results",
                            "No full prompts, answers, or non-arithmetic observations retained"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--math-only", action="store_true")
    parser.add_argument("--output", default="evals/results/s2c/live-comparison-v2.json")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("repeats must be 1..10")
    cases = tuple(c for c in CASES if c[0].startswith("math_")) if args.math_only else CASES
    report = asyncio.run(evaluate(args.repeats, cases))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Real model results:", report["comparison"], "Evidence:", path)


if __name__ == "__main__":
    main()
