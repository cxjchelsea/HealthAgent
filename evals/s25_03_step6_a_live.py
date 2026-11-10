"""S25-03 Step 6-A: real Qwen HTTP saved-plan MCP receipt acceptance.

No fake provider, no model route stubs, no external database. Stores only
bounded verdicts and metadata; never writes model messages or source payloads.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from apps.api.main import create_app
from services.db import AgentAttempt, PlanToolDispatch, database


@contextmanager
def isolated_reader(url):
    engine, Session = database(url)
    try:
        yield Session
    finally:
        engine.dispose()


def evaluate(repeats=2):
    if os.getenv("RUN_S25_03_LIVE") != "1":
        raise RuntimeError("RUN_S25_03_LIVE=1 required")
    if not 1 <= repeats <= 5:
        raise ValueError("repeats must be 1..5")
    base = Settings()
    if base.model_provider not in {"ollama_proxy", "openai_compatible"}:
        raise RuntimeError("real model provider required")
    if not base.model_base_url or base.model_base_url.startswith("https://api.openai.com"):
        raise RuntimeError("an explicit authorized provider endpoint is required")

    runs = []
    with tempfile.TemporaryDirectory(prefix="s25-03-step6-a-") as directory:
        for i in range(repeats):
            filename = Path(directory, f"isolated-{i}.sqlite").resolve()
            runtime = base.model_dump()
            for key in ("route_admission_mode", "understanding_strategy",
                        "m5_unified_route_enabled", "m5_first_route_enabled",
                        "routing_profile"):
                runtime.pop(key, None)
            settings = Settings(**{
                **runtime, "database_url": "sqlite:///" + filename.as_posix(),
                "routing_profile": "unified",
                "route_admission_mode": "guarded",
                "understanding_strategy": "structured",
                "m5_unified_route_enabled": True,
                "m5_first_route_enabled": False,
                "skill_strategy": "all",
                "allow_demo_identity": True,
                "user_tokens": "",
            })
            start = time.monotonic()
            try:
                with (TestClient(create_app(settings)) as client,
                      isolated_reader(settings.database_url) as Session):
                    cid = client.post("/conversations").json()["id"]
                    create = client.post(
                        f"/conversations/{cid}/agent/messages",
                        json={
                            "content": "请创建一个只保存、不执行的两步计划：第一步通过 MCP 参考目录查询 RAG 的定义；第二步计算 6*7。",
                            "client_request_id": "s25-03-create-" + uuid.uuid4().hex},
                        timeout=120)
                    created = create.json()
                    # No direct manipulation of the plan checkpoint or route.
                    valid_create = (
                        create.status_code == 200
                        and created.get("termination_reason") == "plan_created_pending_execution"
                        and len(created.get("plan", [])) == 2
                        and created["plan"][0].get("expected_tool") == "mcp_reference_lookup"
                        and created["plan"][0].get("expected_arguments") == {"term": "RAG"}
                    )
                    executed = None
                    evidence_http = None
                    verified = False
                    actual_dispatch = False
                    if valid_create:
                        first = client.post(
                            f"/conversations/{cid}/agent/messages",
                            json={
                                "content": "现在只执行已保存计划的第一步，第二步不要执行。",
                                "client_request_id": "s25-03-first-" + uuid.uuid4().hex},
                            timeout=120)
                        executed = first.json()
                        with Session() as db:
                            dispatches = db.scalars(select(PlanToolDispatch).where(
                                PlanToolDispatch.conversation_id == cid)).all()
                            attempts = db.scalars(select(AgentAttempt).where(
                                AgentAttempt.conversation_id == cid,
                                AgentAttempt.tool_name == "mcp_reference_lookup")).all()
                        actual_dispatch = (
                            first.status_code == 200
                            and executed.get("termination_reason") == "pending_plan_steps"
                            and len(dispatches) == 1
                            and dispatches[0].status == "checkpointed"
                            and dispatches[0].tool_name == "mcp_reference_lookup"
                            and dispatches[0].tool_version == "legacy-plan-v1"
                            and len(attempts) == 1
                            and attempts[0].id == dispatches[0].id
                            and attempts[0].tool_version == "legacy-plan-v1"
                        )
                        if actual_dispatch:
                            turn_id = attempts[0].turn_id
                            evidence_http = client.get(
                                f"/conversations/{cid}/turns/{turn_id}/evidence")
                            data = evidence_http.json()
                            match = [x for x in data.get("attempts", [])
                                     if x.get("attempt_id") == attempts[0].id]
                            observation = match[0].get("result", {}) if match else {}
                            receipt = json.loads(dispatches[0].receipt_json or "{}")
                            verified = (
                                evidence_http.status_code == 200
                                and len(match) == 1
                                and match[0].get("tool_version") == "legacy-plan-v1"
                                and match[0].get("status") == "succeeded"
                                and isinstance(observation.get("definition"), str)
                                and bool(observation["definition"])
                                and observation["definition"] == receipt.get("definition")
                                and observation.get("source") == receipt.get("source")
                                and observation.get("ok") is True
                                and [s.get("status") for s in executed.get("plan", [])]
                                    == ["completed", "pending"]
                            )
                    runs.append({
                        "repeat": i + 1, "pass": bool(valid_create and actual_dispatch and verified),
                        "creation_pass": bool(valid_create),
                        "dispatch_pass": bool(actual_dispatch),
                        "http_evidence_pass": bool(verified),
                        "create_termination": created.get("termination_reason"),
                        "create_refusal_code": created.get("primary_v1_refusal_code"),
                        "create_validation_issues": [
                            {"field": x.get("field"), "category": x.get("category")}
                            for x in (created.get("primary_v1_validation_issues") or [])[:8]
                            if isinstance(x, dict)],
                        "first_termination": executed.get("termination_reason") if executed else None,
                        "duration_ms": int((time.monotonic() - start) * 1000),
                    })
            except Exception as exc:
                runs.append({
                    "repeat": i + 1, "pass": False,
                    "failure_kind": type(exc).__name__,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                })
    return {
        "suite": "s25-03-step6-a-live-v1", "real_provider": True,
        "provider": base.model_provider, "model": base.model_name,
        "routing_profile": "unified", "repeats": repeats,
        "passed": sum(x["pass"] for x in runs), "total": len(runs),
        "all_passed": all(x["pass"] for x in runs),
        "cases": runs,
        "limitations": [
            "Actual primary routing may reject the chosen two-step plan; failures remain FAIL.",
            "Isolated SQLite; PostgreSQL concurrency and crash tests are separate in CI.",
            "Tests only saved-plan MCP reference and HTTP evidence, not every tool or production.",
            "Report never records definition/source values, memory, tool arguments or model text.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", default="artifacts/s25-03-step6-a-live.json")
    args = parser.parse_args()
    report = evaluate(args.repeats)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"],
                      "all_passed": report["all_passed"], "output": str(out)},
                     ensure_ascii=False))
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
