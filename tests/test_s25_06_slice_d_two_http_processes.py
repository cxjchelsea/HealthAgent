"""S25-06 PostgreSQL physical two-process ASGI HTTP admission race.

Both subprocesses instantiate independent FastAPI apps, SQLAlchemy engines,
sessions and Host admission. This is not a production reverse-proxy test
or real-model test.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyWorkerFence, Message, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)


_CHILD = r"""
import json
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from agent.tool_registry import READ_REGISTRY
import apps.api.main as api

url, cid, request_id, gate_file, marker_file, result_file, slow = sys.argv[1:]
original = READ_REGISTRY.invoke
def blocking(name, *args, **kwargs):
    if slow == "true":
        Path(marker_file).write_text("ready", encoding="utf-8")
        for _ in range(200):
            if Path(gate_file).exists():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("test_release_timeout")
    return original(name, *args, **kwargs)

async def primary(*args, **kwargs):
    text = args[2]
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1", "route_kind": "plan_execute_first",
        "quote": "第一步", "execution_scope": "first_step",
        "effect_intent": "read_only",
    }), text, saved_plan_present=True, pending_action=None, saved_step_count=2)

READ_REGISTRY.invoke = blocking
api.propose_primary_route = primary
cfg = Settings(database_url=url, model_provider="fake", routing_profile="unified",
               primary_route_v1_canary_enabled=True,
               s25_06_dependency_plan_enabled=True)
with TestClient(api.create_app(cfg)) as client:
    response = client.post(f"/conversations/{cid}/agent/messages", json={
        "content": "请只执行已保存计划的第一步",
        "client_request_id": request_id})
    Path(result_file).write_text(json.dumps({
        "code": response.status_code, "body": response.json()
    }), encoding="utf-8")
"""


def _seed_plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "reference", "status": "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "stats", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "text": {"kind": "observation_ref",
                          "source_step_id": 1, "field": "definition"}}},
        ], "executions": {},
    }


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires real PostgreSQL")
def test_two_independent_http_workers_cannot_dispatch_same_step(tmp_path):
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql")
    cid = str(uuid.uuid4())
    engine, Session = database(url)
    plan = json.dumps(_seed_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="demo-local"))
        db.flush()
        db.add(ConversationPlan(conversation_id=cid, snapshot=plan))

    gate, marker = tmp_path / "release", tmp_path / "inside_adapter"
    result_a, result_b = tmp_path / "a.json", tmp_path / "b.json"

    def launch(req, output, slow):
        return subprocess.Popen(
            [sys.executable, "-c", _CHILD, url, cid, req,
             str(gate), str(marker), str(output), "true" if slow else "false"],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    first = None
    second = None
    try:
        first = launch("s2506-worker-one-001", result_a, True)
        deadline = time.monotonic() + 25
        while not marker.exists() and time.monotonic() < deadline:
            if first.poll() is not None:
                raise AssertionError(first.communicate()[1][-3500:])
            time.sleep(0.05)
        assert marker.exists(), "first worker did not enter adapter"
        with Session() as db:
            rows = db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)).all()
            assert len(rows) == 1 and rows[0].status == "dispatch_unknown"

        second = launch("s2506-worker-two-002", result_b, False)
        _, second_err = second.communicate(timeout=35)
        assert second.returncode == 0, second_err[-3500:]
        other = json.loads(result_b.read_text(encoding="utf-8"))
        # Contender may get HTTP conflict or a guarded non-execution response.
        assert other["code"] in {200, 409}, other
        with Session() as db:
            assert db.query(PlanToolDispatch).filter_by(
                conversation_id=cid).count() == 1

        gate.touch()
        _, first_err = first.communicate(timeout=35)
        assert first.returncode == 0, first_err[-3500:]
        first_out = json.loads(result_a.read_text(encoding="utf-8"))
        assert first_out["code"] == 200, first_out
        assert first_out["body"]["termination_reason"] == "dependency_steps_checkpointed"
        with Session() as db:
            saved = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert [s["status"] for s in saved["steps"]] == [
                "completed", "pending"]
            assert db.query(PlanToolDispatch).filter_by(
                conversation_id=cid).count() == 1
            assert db.query(AgentAttempt).filter_by(
                conversation_id=cid).count() == 1
            assert db.get(DependencyWorkerFence, cid).state == "fenced"
            assert db.get(PlanExecutionClaim, cid) is None
    finally:
        gate.touch()
        for proc in (first, second):
            if proc and proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
        with Session.begin() as db:
            for model in (AgentStateTransition, AgentAttempt,
                          AgentTaskState, PlanToolDispatch, PrimaryRouteEvidence,
                          DependencyWorkerFence, PlanExecutionClaim, ConversationPlan,
                          Turn, Message):
                db.execute(delete(model).where(
                    model.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
