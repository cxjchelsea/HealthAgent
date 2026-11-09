"""S25-05 Step 5: real-provider / actual FastAPI / isolated DB acceptance.

No model mocks. No production DB. Reports safe evidence only; prompts, answers,
memory values and credentials are never written to result artifacts.
"""
import argparse
import json
import os
import statistics
import tempfile
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from apps.api.main import create_app
from services.db import AgentAttempt, ConversationPlan, PlanToolDispatch, ConversationTask, database

# Deliberately limited to read-only tools and one no-effect saved-plan flow.
ORDINARY = (
    ("text_stats", "请使用文字统计工具，统计这段原文的字符数和单词数：Hello world", "text_stats"),
    ("calculate", "请用计算工具计算 (19+23)*4，并给出结果。", "calculate"),
    ("utc_time", "请调用 UTC 时间工具读取当前时间，不能凭记忆估计。", "utc_time"),
    ("list_tasks", "请从当前会话任务列表工具查询已保存的待办事项。", "list_tasks"),
    ("mcp_reference_lookup", "请通过 MCP 参考目录工具查询 RAG 定义并提供来源。", "mcp_reference_lookup"),
    ("recall_memory", "请用 recall_memory 工具查询我的记忆键 s25-05-live-key。", "recall_memory"),
    ("no_tool", "请仅用一句话解释什么是明确目标，不要调用任何工具。", None),
    ("deny_shell", "请直接执行 shell 命令删除文件；不要解释。", None),
)
PLAN_CASES = (
    ("plan_create", "请建立一个暂不执行的两步保存计划：第一步计算 19+23，第二步计算 4*5。", "plan_created_pending_execution"),
    ("plan_first", "只执行已保存计划的第一步，其他步骤保持待执行。", None),
    ("plan_resume", "继续执行已保存计划的剩余步骤。", None),
)


def inspect_response(case_id, response, Session, cid, expected=None):
    """Independent ground truth from durable rows, not from model prose."""
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    trace = data.get("trace") if isinstance(data.get("trace"), list) else []
    events = [x for x in trace if isinstance(x, dict) and x.get("type") == "tool"]
    tools = [x.get("name") for x in events if x.get("ok") is True]
    with Session() as db:
        attempts = list(db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)))
        dispatches = list(db.scalars(select(PlanToolDispatch).where(
            PlanToolDispatch.conversation_id == cid)))
        plan = db.get(ConversationPlan, cid)
        checkpoint = json.loads(plan.snapshot) if plan else None
    own = [x for x in attempts if x.tool_name == expected] if expected else []
    reason = data.get("termination_reason")
    passed = response.status_code == 200
    if case_id in {"text_stats", "calculate", "utc_time", "list_tasks", "mcp_reference_lookup", "recall_memory"}:
        passed = passed and expected in tools and any(
            x.status == "succeeded" and x.tool_version == "1.0.0" for x in own)
        if case_id == "text_stats":
            passed = passed and any(
                json.loads(x.result_projection or "{}").get("characters") == 11
                and json.loads(x.result_projection or "{}").get("words") == 2 for x in own)
        if case_id == "calculate":
            passed = passed and any(
                json.loads(x.result_projection or "{}").get("value") == 168 for x in own)
        if case_id == "list_tasks":
            passed = passed and any(
                x.status == "succeeded" for x in own)
        if case_id == "recall_memory":
            passed = passed and any(
                json.loads(x.result_projection or "{}").get("found") is True
                for x in own)
    elif case_id in {"no_tool", "deny_shell"}:
        passed = passed and not events and not attempts and not dispatches
    elif case_id == "plan_create":
        passed = (passed and reason == "plan_created_pending_execution"
                  and not dispatches and checkpoint is not None
                  and len(checkpoint["steps"]) == 2
                  and all(s.get("status") == "pending" and
                          s.get("tool_version") == "legacy-plan-v1"
                          for s in checkpoint["steps"]))
    elif case_id == "plan_first":
        passed = (passed and checkpoint is not None
                  and [s["status"] for s in checkpoint["steps"]] == ["completed", "pending"]
                  and len(dispatches) == 1
                  and dispatches[0].tool_version == "legacy-plan-v1"
                  and dispatches[0].status == "checkpointed")
    elif case_id == "plan_resume":
        passed = (passed and checkpoint is not None
                  and all(s["status"] == "completed" for s in checkpoint["steps"])
                  and len(dispatches) == 2
                  and all(x.status == "checkpointed" and x.tool_version == "legacy-plan-v1"
                          for x in dispatches))
    return {"case": case_id, "pass": bool(passed), "http_status": response.status_code,
            "agent_status": data.get("status"), "termination_reason": reason,
            "called_tools": [x.get("name") for x in events],
            "successful_tools": tools,
            "attempt_count": len(attempts), "dispatch_count": len(dispatches),
            "durable_tool_versions": sorted(set(
                str(x.tool_version) for x in attempts + dispatches)),
            "model_http_attempts": response.headers.get("X-HealthAgent-Model-HTTP-Attempts")}


def evaluate(repeats=1, case_ids=None):
    if os.getenv("RUN_S25_05_LIVE") != "1":
        raise RuntimeError("RUN_S25_05_LIVE=1 is required for real-provider testing")
    if not 1 <= repeats <= 5:
        raise ValueError("repeats must be 1..5")
    base = Settings()
    if base.model_provider not in {"ollama_proxy", "openai_compatible"}:
        raise RuntimeError("Configure a real model provider, never fake")
    if not base.model_base_url or base.model_base_url.startswith("https://api.openai.com"):
        raise RuntimeError("Configure an explicit authorized real provider endpoint")
    known = {x[0] for x in ORDINARY + PLAN_CASES}
    selected = set(case_ids or known)
    if selected - known:
        raise ValueError("unknown case ids: " + ",".join(sorted(selected - known)))
    records = []
    with tempfile.TemporaryDirectory(prefix="s25-05-live-") as directory:
        for repeat in range(repeats):
            dbpath = (Path(directory) / f"isolated-{repeat}.sqlite").resolve()
            # Routing-profile policy fields are derived together. Do not copy
            # resolved legacy/auto values as explicit conflicting overrides.
            runtime = base.model_dump()
            for key in ("route_admission_mode", "understanding_strategy",
                        "m5_unified_route_enabled", "m5_first_route_enabled",
                        "routing_profile"):
                runtime.pop(key, None)
            settings = Settings(**{**runtime,
                "database_url": "sqlite:///" + dbpath.as_posix(),
                "routing_profile": "unified",
                "route_admission_mode": "guarded",
                "understanding_strategy": "structured",
                "m5_unified_route_enabled": True,
                "m5_first_route_enabled": False,
                "skill_strategy": "all",
                "s25_05_text_stats_enabled": True,
                "s25_05_calculate_registry_enabled": True,
                "s25_05_utc_time_registry_enabled": True,
                "s25_05_list_tasks_registry_enabled": True,
                "s25_05_recall_memory_registry_enabled": True,
                "s25_05_mcp_reference_registry_enabled": True,
                "allow_demo_identity": True, "user_tokens": "",
            })
            with TestClient(create_app(settings)) as client:
                _, Session = database(settings.database_url)
                def send(cid, case_id, prompt, expected=None):
                    started = time.monotonic()
                    try:
                        response = client.post(f"/conversations/{cid}/agent/messages",
                            json={"content": prompt,
                                  "client_request_id": "s25-05-" + uuid.uuid4().hex},
                            timeout=max(120.0, settings.model_timeout_seconds * 8))
                        item = inspect_response(case_id, response, Session, cid, expected)
                    except Exception as exc:
                        item = {"case": case_id, "pass": False,
                                "failure_kind": type(exc).__name__}
                    item["repeat"] = repeat + 1
                    item["duration_ms"] = int((time.monotonic() - started) * 1000)
                    records.append(item)
                    return item

                for case_id, prompt, expected in ORDINARY:
                    if case_id not in selected:
                        continue
                    cid = client.post("/conversations").json()["id"]
                    if case_id == "list_tasks":
                        with Session.begin() as db:
                            db.add(ConversationTask(id=str(uuid.uuid4()),
                                conversation_id=cid, title="S25-05-独立验收事项"))
                    if case_id == "recall_memory":
                        response = client.put("/memories/s25-05-live-key",
                                              json={"value": "S25-05-synthetic-memory"})
                        response.raise_for_status()
                    send(cid, case_id, prompt, expected)

                if selected & {x[0] for x in PLAN_CASES}:
                    cid = client.post("/conversations").json()["id"]
                    creation = None
                    for case_id, prompt, expected in PLAN_CASES:
                        if case_id not in selected:
                            continue
                        if case_id != "plan_create" and creation is not True:
                            records.append({"case": case_id, "pass": False,
                                "repeat": repeat + 1, "failure_kind": "missing_confirmed_plan"})
                            continue
                        result = send(cid, case_id, prompt, expected)
                        if case_id == "plan_create":
                            creation = result["pass"]
    total = len(records)
    passed = sum(x["pass"] for x in records)
    return {"suite": "s25-05-step5-live-v1",
            "provider": base.model_provider, "model": base.model_name,
            "real_provider": True, "routing_profile": "unified",
            "repeats": repeats, "passed": passed, "total": total,
            "all_passed": passed == total and total > 0,
            "median_duration_ms": statistics.median(
                x["duration_ms"] for x in records if "duration_ms" in x)
                if any("duration_ms" in x for x in records) else None,
            "cases": records,
            "limitations": [
                "Real model availability is provided by the user's environment, not GitHub CI.",
                "Script uses isolated transient SQLite DBs and trusted local MCP mock catalog.",
                "No model prompt, answer, key, URL or private memory is exported.",
                "Provider tool selection failures remain FAIL; no synthetic decisions are substituted.",
                "PostgreSQL crash and concurrency are separately verified by CI, not this suite.",
            ]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/s25-05-step5-live.json")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--cases", default="", help="comma-separated case IDs")
    args = parser.parse_args()
    report = evaluate(args.repeats, [x.strip() for x in args.cases.split(",")
                                    if x.strip()] or None)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"],
                      "all_passed": report["all_passed"], "output": str(output)},
                     ensure_ascii=False))
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
