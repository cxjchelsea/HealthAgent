"""S2-D live Qwen two-turn acceptance through the real conversation HTTP API.

Uses an isolated temporary SQLite DB and the actual NativeToolDecision.
No mocked decisions; reports checks without saving raw response text.
"""
import argparse
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config import Settings
from apps.api.main import create_app
from services.db import ConversationPlan, Turn, database

FIRST_GOAL = "请先用 propose_plan 规划计算8*8，再调用 calculate 计算并验证结果。"
SECOND_GOAL = "更正刚才的目标：不是8*8，而是9*9。请使用 revise_plan 修订原计划，然后用 calculate 计算。"


def _step_matches(step, expression):
    return (step.get("status") == "completed"
            and step.get("expected_tool") == "calculate"
            and step.get("expected_arguments") == {"expression": expression}
            and step.get("evidence_tool") == "calculate")


def _plan_checks(first, second, first_checkpoint, final_checkpoint, messages, turns):
    first_steps = (first_checkpoint or {}).get("steps", [])
    final_steps = (final_checkpoint or {}).get("steps", [])
    first_rev = (first_checkpoint or {}).get("revision", 0)
    final_rev = (final_checkpoint or {}).get("revision", 0)
    return {
        "first_http_200": first["http_status"] == 200,
        "first_plan_verified": any(_step_matches(x, "8*8") for x in first_steps),
        "first_checkpoint_saved": first_rev >= 1,
        "second_http_200": second["http_status"] == 200,
        "revision_advanced": final_rev > first_rev >= 1,
        "correction_verified": any(_step_matches(x, "9*9") for x in final_steps),
        "old_calculation_invalidated": not any(
            x.get("status") == "completed" and x.get("expected_arguments") == {"expression": "8*8"}
            for x in final_steps),
        "messages_persisted": len(messages) == 4 and
            [x["role"] for x in messages] == ["user", "assistant", "user", "assistant"],
        "turns_terminal": len(turns) == 2 and
            all(x in {"completed", "partial"} for x in turns),
    }


def evaluate():
    if os.getenv("RUN_S2D_LIVE") != "1":
        raise RuntimeError("Set RUN_S2D_LIVE=1 to use the real Qwen provider")
    settings = Settings()
    if settings.model_provider not in {"ollama_proxy", "openai_compatible"}:
        raise RuntimeError("A real model provider is required; fake provider is not valid")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="healthagent-s2d-api-") as folder:
        db_url = "sqlite:///" + str(Path(folder) / "acceptance.sqlite3")
        # Use real provider settings but never the configured application database.
        isolated = settings.model_copy(update={
            "database_url": db_url, "allow_demo_identity": True,
            "user_tokens": "", "skill_strategy": "dynamic", "skill_budget_chars": 2000,
        })
        outcomes = []
        checkpoints = []
        with TestClient(create_app(isolated)) as client:
            created = client.post("/conversations")
            created.raise_for_status()
            cid = created.json()["id"]
            for goal in (FIRST_GOAL, SECOND_GOAL):
                response = client.post(
                    f"/conversations/{cid}/agent/messages",
                    json={"content": goal, "client_request_id": str(uuid.uuid4())})
                payload = response.json()
                outcomes.append({
                    "http_status": response.status_code,
                    "agent_status": payload.get("status"),
                    "trace_tools": [item.get("tools") for item in payload.get("trace", [])
                                    if item.get("type") == "decision"],
                    "plan_events": [item.get("event") for item in payload.get("plan_events", [])],
                    "revision_diagnostics": [
                        {"revision": item.get("revision"),
                         "invalidated": item.get("invalidated"),
                         "preserved": item.get("preserved")}
                        for item in payload.get("plan_events", [])
                        if item.get("event") == "plan_revised"],
                    "error_code": payload.get("detail") if response.status_code != 200 else None,
                })
                engine, Session = database(db_url)
                try:
                    with Session() as db:
                        record = db.get(ConversationPlan, cid)
                        checkpoints.append(json.loads(record.snapshot) if record else None)
                finally:
                    engine.dispose()
                # Stop on failed first turn: a correction without first checkpoint
                # would not constitute a multi-turn resume test.
                if response.status_code != 200:
                    break
            persisted = client.get(f"/conversations/{cid}").json().get("messages", [])
        engine, Session = database(db_url)
        try:
            with Session() as db:
                rows = db.query(Turn).filter_by(conversation_id=cid).all()
                turns = [r.state for r in rows]
        finally:
            engine.dispose()
        first = outcomes[0] if outcomes else {"http_status": 0}
        second = outcomes[1] if len(outcomes) == 2 else {"http_status": 0}
        checks = _plan_checks(first, second,
                              checkpoints[0] if checkpoints else None,
                              checkpoints[-1] if checkpoints else None,
                              persisted, turns)
        return {
            "suite": "s2d-conversation-api-live-v2",
            "mode": "REAL_PROVIDER", "provider": settings.model_provider,
            "model": settings.model_name,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "checks": checks, "passed": all(checks.values()),
            "turns": outcomes,
            "checkpoint_revisions": [x.get("revision") if x else None for x in checkpoints],
            "final_plan_steps": [
                {"id": x.get("id"), "status": x.get("status"),
                 "expected_tool": x.get("expected_tool"),
                 "expected_arguments": x.get("expected_arguments")}
                for x in ((checkpoints[-1] or {}).get("steps", []) if checkpoints else [])],
            "database": "isolated_temporary_sqlite",
            "limitations": [
                "Single real-provider sequence, not a reliability estimate",
                "Does not establish multi-process or PostgreSQL behavior",
                "Response content and credentials are not exported",
            ],
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evals/results/s2d/qwen-conversation-api-v2.json")
    args = parser.parse_args()
    report = evaluate()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("S2-D official API live E2E:", "PASS" if report["passed"] else "FAIL", path)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
