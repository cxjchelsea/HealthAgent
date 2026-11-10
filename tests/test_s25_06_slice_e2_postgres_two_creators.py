"""S25-06 E2 physical two-process HTTP creation CAS race on PostgreSQL."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete

from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyWorkerFence, Message, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)


_CHILD = r'''
import json
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
import apps.api.main as api

url, cid, req, marker, gate, outfile = sys.argv[1:]
message = "请先查询 RAG 的定义，再统计查到的文本字符数。只保存计划，暂不执行。"
plan = {
    "plan_schema_version": "dependency-plan-v1",
    "plan_executor_version": "dependency-plan-read-v1",
    "revision": 1, "next_step_id": 3,
    "steps": [
        {"id": 1, "goal": "查询 RAG 定义", "depends_on": [],
         "status": "pending", "expected_tool": "mcp_reference_lookup",
         "tool_version": "1.0.0",
         "argument_bindings": {"term": {
             "kind": "literal", "value": "RAG", "source_quote": "RAG"}}},
        {"id": 2, "goal": "统计定义字符", "depends_on": [1],
         "status": "pending", "expected_tool": "text_stats",
         "tool_version": "1.0.0",
         "argument_bindings": {"text": {
             "kind": "observation_ref", "source_step_id": 1,
             "field": "definition"}}},
    ], "executions": {},
}

async def primary(provider, model, current, **kwargs):
    assert current == message and kwargs["saved_plan_present"] is False
    assert kwargs["dependency_plan_enabled"] is True
    proposal = bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1", "route_kind": "plan_create",
        "quote": current, "execution_scope": "none",
        "effect_intent": "mutation_requested", "steps": [],
        "dependency_plan": plan,
    }), current, saved_plan_present=False, pending_action=None,
        dependency_plan_enabled=True)
    Path(marker).touch()
    deadline = time.monotonic() + 25
    while not Path(gate).exists() and time.monotonic() < deadline:
        await __import__("asyncio").sleep(.05)
    if not Path(gate).exists():
        raise RuntimeError("barrier timeout")
    return proposal

api.propose_primary_route = primary
cfg = Settings(database_url=url, model_provider="fake",
               routing_profile="unified", primary_route_v1_canary_enabled=True,
               s25_06_dependency_plan_enabled=True)
with TestClient(api.create_app(cfg)) as client:
    result = client.post(f"/conversations/{cid}/agent/messages", json={
        "content": message, "client_request_id": req})
    Path(outfile).write_text(json.dumps({
        "http": result.status_code, "response": result.json()
    }), encoding="utf-8")
'''


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires isolated real PostgreSQL")
def test_two_independent_http_workers_create_exactly_one_plan(tmp_path):
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql")
    engine, Session = database(url)
    cid = str(uuid.uuid4())
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="demo-local"))
    gate = tmp_path / "go"
    markers = [tmp_path / "a.ready", tmp_path / "b.ready"]
    outputs = [tmp_path / "a.json", tmp_path / "b.json"]
    procs = []
    try:
        for i in range(2):
            procs.append(subprocess.Popen([
                sys.executable, "-c", _CHILD, url, cid,
                f"e2-pg-create-{i}-{uuid.uuid4()}", str(markers[i]),
                str(gate), str(outputs[i])],
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        deadline = time.monotonic() + 25
        while not all(p.exists() for p in markers) and time.monotonic() < deadline:
            for proc in procs:
                if proc.poll() is not None:
                    raise AssertionError(proc.communicate()[1][-3500:])
            time.sleep(.05)
        assert all(p.exists() for p in markers), "workers did not enter create"
        gate.touch()
        for proc in procs:
            _, stderr = proc.communicate(timeout=35)
            assert proc.returncode == 0, stderr[-3500:]
        results = [json.loads(p.read_text(encoding="utf-8")) for p in outputs]
        assert all(x["http"] == 200 for x in results), results
        reasons = [x["response"]["termination_reason"] for x in results]
        assert sorted(reasons) == sorted([
            "dependency_plan_created_pending_execution",
            "dependency_plan_create_conflict"])
        with Session() as db:
            saved = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert saved["plan_schema_version"] == "dependency-plan-v1"
            assert [x["status"] for x in saved["steps"]] == ["pending", "pending"]
            assert db.query(PlanToolDispatch).filter_by(conversation_id=cid).count() == 0
            assert db.query(AgentAttempt).filter_by(conversation_id=cid).count() == 0
            assert db.query(PlanExecutionClaim).filter_by(conversation_id=cid).count() == 0
            assert db.query(Turn).filter_by(conversation_id=cid, state="running").count() == 0
            assert db.query(PrimaryRouteEvidence).filter_by(conversation_id=cid).count() == 2
    finally:
        gate.touch()
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
        with Session.begin() as db:
            for model in (
                    AgentStateTransition, AgentAttempt, AgentTaskState,
                    PlanToolDispatch, PrimaryRouteEvidence,
                    DependencyWorkerFence, PlanExecutionClaim, ConversationPlan,
                    Turn, Message):
                db.execute(delete(model).where(model.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
