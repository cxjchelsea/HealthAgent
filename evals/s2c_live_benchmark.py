"""Paired real-provider S2-C experiment using the EXISTING AgentLoop.

No synthetic decisions. Requires RUN_S2C_LIVE=1 plus an actual provider configuration.
Produces redacted per-case evidence and compares dynamic vs all exposure.
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
    ("implicit_math", "18 加上 24 再乘 3 的计算结果是什么？请用工具。", "calculate", "126"),
    ("multi_tool", "请先查询 RAG 的术语解释，再算一下 8*8。", None, None),
    ("unknown_reference", "请通过参考工具查询不存在的术语 xyznothing。", "mcp_reference_lookup", None),
    ("unsupported", "请执行 shell 命令删除文件。", None, None),
)

async def measure(strategy, case, settings):
    case_id, goal, expected_tool, answer_marker = case
    started = time.monotonic()
    try:
        outcome = await AgentLoop(
            NativeToolDecision(settings), mcp_client=StdioMCPClient(),
            skill_strategy=strategy, max_steps=6
        ).run(goal, include_evidence=True)
        audit = outcome.get("audit", [])
        attempted = [item["name"] for item in audit]
        success = outcome["status"] == "completed"
        if expected_tool:
            success = success and expected_tool in attempted
            if case_id != "unknown_reference":
                success = success and any(
                    item["name"] == expected_tool and item["observation"].get("ok")
                    for item in audit
                )
        if answer_marker:
            success = success and answer_marker in outcome["answer"].casefold()
        if case_id == "multi_tool":
            success = success and {"calculate", "mcp_reference_lookup"}.issubset(set(attempted))
        if case_id == "unknown_reference":
            success = success and any(
                item["name"] == "mcp_reference_lookup" and not item["observation"].get("ok")
                for item in audit
            )
        if case_id == "unsupported":
            success = success and not attempted
        return {"id": case_id, "strategy": strategy, "passed": success,
                "status": outcome["status"], "tools": attempted,
                "tool_success": sum(bool(x["observation"].get("ok")) for x in audit),
                "steps": outcome.get("steps"),
                "latency_ms": round((time.monotonic()-started)*1000),
                "error": None}
    except Exception as exc:
        return {"id": case_id, "strategy": strategy, "passed": False,
                "status": "exception", "tools": [], "tool_success": 0,
                "steps": None, "latency_ms": round((time.monotonic()-started)*1000),
                "error": type(exc).__name__}

async def evaluate(repeats=2):
    if os.getenv("RUN_S2C_LIVE") != "1":
        raise RuntimeError("Set RUN_S2C_LIVE=1 to authorize provider calls")
    settings = Settings()
    if settings.model_provider not in ("ollama_proxy", "openai_compatible"):
        raise RuntimeError("Configure a real tool-calling provider")
    rows = []
    # Counterbalance which strategy runs first, to avoid fixed-order advantage.
    for repeat in range(repeats):
        for case in CASES:
            order = ("all", "dynamic") if repeat % 2 else ("dynamic", "all")
            for strategy in order:
                rows.append(await measure(strategy, case, settings))
    by_strategy = {}
    for strategy in ("all", "dynamic"):
        group = [r for r in rows if r["strategy"] == strategy]
        by_strategy[strategy] = {
            "attempts": len(group),
            "passed": sum(r["passed"] for r in group),
            "task_success_rate": sum(r["passed"] for r in group) / len(group),
            "median_latency_ms": statistics.median(r["latency_ms"] for r in group),
            "tool_call_count": sum(len(r["tools"]) for r in group),
        }
    return {"mode": "REAL_PROVIDER", "model": settings.model_name,
            "provider": settings.model_provider, "repeats": repeats,
            "results": rows, "comparison": by_strategy,
            "limitations": ["Small nonrandomized sample",
                            "Answer grading uses limited keyword checks",
                            "No vendor token telemetry or confidence interval",
                            "Report intentionally omits prompts, observations and answers"]}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", default="evals/results/s2c/live-comparison-v1.json")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("repeats must be 1..10")
    report = asyncio.run(evaluate(args.repeats))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Real model results:", report["comparison"], "Evidence:", path)

if __name__ == "__main__":
    main()
