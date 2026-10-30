"""S25-04 real-provider strategy comparison through official Conversation API.

Run from repo root with RUN_S25_04_LIVE=1 and an accessible real provider.
No model responses, prompts or memory contents are exported.
"""
import json
import os
import statistics
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config import Settings
from apps.api.main import create_app

# Fixed, non-write tasks with ground-truth observable tool requirements.
CASES = (
    ("implicit_tasks", "我之前列过哪些待办事项？请查看记录再回答。", {"list_tasks"}),
    ("implicit_calculation", "七件事已经完成五件，完成率是多少？请核算。", {"calculate"}),
    ("reference_lookup", "请在现有参考目录里核实 MCP 一词，注明来源。", {"mcp_reference_lookup"}),
    ("utc_now", "请核实此刻 UTC 时间。", {"utc_time"}),
    ("no_tool", "用自己的话简要解释行动和目标的区别。", set()),
)
STRATEGIES = ("dynamic", "all", "semantic", "semantic_guided", "semantic_topk")


def evaluate(repeats=2):
    if os.environ.get("RUN_S25_04_LIVE") != "1":
        raise RuntimeError("Set RUN_S25_04_LIVE=1 to authorize real-model requests")
    if repeats < 1 or repeats > 10:
        raise ValueError("repeats must be between 1 and 10")
    base = Settings()
    if base.model_provider == "fake":
        raise RuntimeError("REAL_PROVIDER required: model_provider=fake is not valid")
    selected_ids = {x.strip() for x in os.getenv("S25_04_CASE_IDS", "").split(",") if x.strip()}
    selected_strategies = {x.strip() for x in os.getenv("S25_04_STRATEGIES", "").split(",") if x.strip()}
    if selected_strategies - set(STRATEGIES):
        raise ValueError("Unknown S25_04_STRATEGIES")
    unknown = selected_ids - {row[0] for row in CASES}
    if unknown:
        raise ValueError("Unknown S25_04_CASE_IDS values: " + ",".join(sorted(unknown)))
    results = []
    for strategy in STRATEGIES:
        if selected_strategies and strategy not in selected_strategies:
            continue
        for case_id, goal, expected in CASES:
            if selected_ids and case_id not in selected_ids:
                continue
            for repetition in range(repeats):
                database_url = "sqlite:///" + str(
                    Path("evals/results/s25-04") / f"trial-{uuid.uuid4().hex}.db"
                )
                Path("evals/results/s25-04").mkdir(parents=True, exist_ok=True)
                settings = Settings(
                    **{**base.model_dump(), "skill_strategy": strategy,
                       "database_url": database_url, "skill_budget_chars": 2000}
                )
                with TestClient(create_app(settings)) as client:
                    conv = client.post("/conversations")
                    conv.raise_for_status()
                    cid = conv.json()["id"]
                    started = time.monotonic()
                    response = client.post(
                        f"/conversations/{cid}/agent/messages",
                        json={"content": goal, "client_request_id": str(uuid.uuid4())},
                    )
                    duration_ms = round((time.monotonic() - started) * 1000)
                    payload = response.json()
                trace = payload.get("trace") or []
                tool_events = [e for e in trace if e.get("type") == "tool"]
                actual = {e.get("name") for e in tool_events if e.get("ok")}
                first_tools = next(
                    (e.get("tool_names", []) for e in trace
                     if e.get("type") == "tool_exposure"),
                    [],
                )
                # Legacy modes expose fixed tools but currently do not record
                # tool_exposure trace. Do not infer their recall from this field.
                recall = (expected <= set(first_tools)
                          if strategy in ("semantic", "semantic_guided", "semantic_topk") else None)
                results.append({
                    "case_id": case_id,
                    "strategy": strategy,
                    "repeat": repetition + 1,
                    "http_status": response.status_code,
                    "agent_terminal_success": (response.status_code == 200 and payload.get("status") == "completed"),
                    "agent_status": payload.get("status"),
                    "termination_reason": payload.get("termination_reason"),
                    "execution_mode": payload.get("execution_mode"),
                    "trace_types": [e.get("type") for e in trace],
                    "first_exposed_tools": first_tools if strategy in ("semantic", "semantic_guided") else None,
                    "duration_ms": duration_ms,
                    "tools_called": [e.get("name") for e in tool_events],
                    "success_tools": sorted(str(t) for t in actual),
                    "candidate_recall": recall,
                    "tool_evidence_matched": (expected <= actual if expected else not tool_events),
                    "no_tool_violations": (len(tool_events) if not expected else 0),
                    "reason_code": (payload.get("detail", {}).get("code")
                                    if isinstance(payload.get("detail"), dict) else None),
                })
                # Delete ephemeral test DB after client disposal.
                Path(database_url.removeprefix("sqlite:///")).unlink(missing_ok=True)
    summary = {}
    for strategy in STRATEGIES:
        if selected_strategies and strategy not in selected_strategies:
            continue
        rows = [x for x in results if x["strategy"] == strategy]
        summary[strategy] = {
            "cases": len(rows),
            "http_200": sum(x["http_status"] == 200 for x in rows),
            "agent_completed": sum(x["agent_terminal_success"] for x in rows),
            "partial": sum(x["agent_status"] == "partial" for x in rows),
            "tool_evidence_matched": sum(x["tool_evidence_matched"] for x in rows),
            "no_tool_violations": sum(x["no_tool_violations"] for x in rows),
            "median_duration_ms": statistics.median(x["duration_ms"] for x in rows),
            "candidate_recall_measured": sum(x["candidate_recall"] is not None
                                             for x in rows),
        }
    return {
        "suite": "s25-04-live-comparison-v1",
        "mode": "REAL_PROVIDER",
        "provider": base.model_provider,
        "model": base.model_name,
        "repeats": repeats,
        "selected_case_ids": sorted(selected_ids) if selected_ids else "all",
        "selected_strategies": sorted(selected_strategies) if selected_strategies else "all",
        "summary": summary,
        "cases": results,
        "limitations": [
            "Tool evidence is a proxy; final answer semantic quality is not auto-scored.",
            "This provider adapter does not expose token usage; token cost not measured.",
            "One-case conversations do not measure long-horizon multi-turn planning.",
            "Legacy strategies have no candidate exposure trace; recall not inferred.",
            "Host routing may terminate before AgentLoop; trace and termination distinguish it.",
            "No preseeded tasks; task lookup outcome may reflect absent records.",
            "SQLite isolated per run, no external production data or write operations.",
            "Live execution requires user environment with configured provider access.",
        ],
    }


def main():
    repeats = int(os.getenv("S25_04_REPEATS", "2"))
    report = evaluate(repeats=repeats)
    target = Path("evals/results/s25-04/live-comparison-v1.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("Evidence:", target)
    if any(x["http_status"] != 200 or not x["agent_terminal_success"]
           or not x["tool_evidence_matched"] for x in report["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
