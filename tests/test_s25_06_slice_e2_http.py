"""S25-06 Slice E2: real Conversation HTTP creation/atomic CAS without tools."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from agent.primary_host_admission_v1 import PrimaryHostFacts, admit_primary_route_v1
from apps.api.main import create_app
from services.db import (
    AgentAttempt, ConversationPlan, DependencyWorkerFence, Message,
    PlanExecutionClaim, PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)
from services.plan_store import ConversationPlanStore

CREATE = "请先查询 RAG 的定义，再统计查到的文本字符数。只保存计划，暂不执行。"
FIRST = "请只执行已保存计划的第一步"
RESUME = "请继续执行已保存计划的剩余步骤"


def _plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询 RAG 定义", "depends_on": [],
             "status": "pending", "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "统计定义字符", "depends_on": [1],
             "status": "pending", "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "text": {"kind": "observation_ref",
                          "source_step_id": 1, "field": "definition"}}},
        ], "executions": {},
    }


def _config(tmp_path, enabled=True):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'e2-http.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=enabled,
    )


def _create_signal(message=CREATE):
    return {
        "schema_version": "primary-route-v1", "route_kind": "plan_create",
        "quote": message, "execution_scope": "none",
        "effect_intent": "mutation_requested", "steps": [],
        "dependency_plan": _plan(),
    }


def _bound(message, saved):
    if message == CREATE:
        return bind_route_signal(
            RouteSignal.model_validate(_create_signal()), message,
            saved_plan_present=saved, pending_action=None,
            dependency_plan_enabled=True)
    first = message == FIRST
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "plan_execute_first" if first else "plan_resume",
        "quote": message,
        "execution_scope": "first_step" if first else "remaining_steps",
        "effect_intent": "read_only",
    }), message, saved_plan_present=saved, pending_action=None,
       saved_step_count=2)


def _install_primary(monkeypatch):
    seen = []

    async def primary(provider, model, message, **kwargs):
        seen.append((message, kwargs.get("dependency_plan_enabled")))
        return _bound(message, kwargs["saved_plan_present"])

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    return seen


def test_e2_http_create_then_first_resume_uses_same_primary_and_no_create_tool(
        tmp_path, monkeypatch):
    seen = _install_primary(monkeypatch)
    from agent.tool_registry import READ_REGISTRY
    original = READ_REGISTRY.invoke
    calls = []

    def invoke(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(READ_REGISTRY, "invoke", invoke)
    cfg = _config(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        creation = client.post(f"/conversations/{cid}/agent/messages",
                               json={"content": CREATE,
                                     "client_request_id": "e2-create-001"})
        assert creation.status_code == 200, creation.text
        assert creation.json()["termination_reason"] == (
            "dependency_plan_created_pending_execution")
        assert not calls
        _, Session = database(cfg.database_url)
        with Session() as db:
            saved = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert saved == _plan()
            assert db.get(PrimaryRouteEvidence, creation.json()["turn_id"]).admission_reason == (
                "dependency_create_candidate")
            assert db.get(Turn, creation.json()["turn_id"]).state == "partial"
            assert db.query(PlanToolDispatch).count() == 0
            assert db.query(PlanExecutionClaim).count() == 0
            assert db.query(DependencyWorkerFence).count() == 0
            assert db.query(AgentAttempt).count() == 0
        first = client.post(f"/conversations/{cid}/agent/messages",
                            json={"content": FIRST, "client_request_id": "e2-first-001"})
        assert first.status_code == 200, first.text
        assert first.json()["termination_reason"] == "dependency_steps_checkpointed"
        second = client.post(f"/conversations/{cid}/agent/messages",
                             json={"content": RESUME, "client_request_id": "e2-resume-001"})
        assert second.status_code == 200, second.text
        assert second.json()["termination_reason"] == "dependency_steps_checkpointed"
        with Session() as db:
            final = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert [step["status"] for step in final["steps"]] == [
                "completed", "completed"]
            assert db.query(AgentAttempt).count() == 2
            assert db.query(PlanToolDispatch).count() == 2
            assert db.get(PlanExecutionClaim, cid) is None
        assert len(calls) == 2
    assert seen == [(CREATE, True), (FIRST, False), (RESUME, False)]


def test_e2_model_creation_conflict_preserves_first_writer_and_no_false_success(
        tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    competing = {"revision": 1, "steps": []}

    async def racing_primary(provider, model, message, **kwargs):
        # Simulate another HTTP worker saving after this Turn captured absence,
        # before this Turn attempts its terminal CAS.
        with Session.begin() as db:
            db.add(ConversationPlan(conversation_id=cid,
                                    snapshot=json.dumps(competing)))
        return _bound(message, saved=False)

    monkeypatch.setattr("apps.api.main.propose_primary_route", racing_primary)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        response = client.post(f"/conversations/{cid}/agent/messages",
                               json={"content": CREATE,
                                     "client_request_id": "e2-race-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "dependency_plan_create_conflict"
        assert "已保存" not in response.json()["answer"]
        with Session() as db:
            assert json.loads(db.get(ConversationPlan, cid).snapshot) == competing
            assert db.get(Turn, response.json()["turn_id"]).state == "partial"
            assert db.query(PlanToolDispatch).count() == 0
            assert db.query(PlanExecutionClaim).count() == 0
            assert db.query(AgentAttempt).count() == 0


def test_e2_host_denies_untrusted_forged_bound_plan(tmp_path):
    proposal = _bound(CREATE, saved=False)
    good = admit_primary_route_v1(proposal, PrimaryHostFacts(
        checkpoint=None, pending_action=None, pending_origin_verified=False,
        guarded_mode=True, turn_created=True,
        current_request=CREATE, dependency_plan_enabled=True))
    assert (good.disposition, good.reason) == ("allow", "dependency_create_candidate")
    for kwargs in (
        {"dependency_plan_enabled": False},
        {"pending_action": "create_task"},
        {"checkpoint": {"revision": 1}},
        {"current_request": "请创建一个完全不同的计划"},
        {"guarded_mode": False},
    ):
        facts = {
            "checkpoint": None, "pending_action": None,
            "pending_origin_verified": False,
            "guarded_mode": True, "turn_created": True,
            "current_request": CREATE,
            "dependency_plan_enabled": True,
        }
        facts.update(kwargs)
        outcome = admit_primary_route_v1(proposal, PrimaryHostFacts(**facts))
        assert outcome.disposition != "allow"


def test_e2_new_create_flag_off_cannot_save(tmp_path, monkeypatch):
    async def unsafe_proposal(*args, **kwargs):
        return _bound(CREATE, saved=False)

    monkeypatch.setattr("apps.api.main.propose_primary_route", unsafe_proposal)
    cfg = _config(tmp_path, enabled=False)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages",
                           json={"content": CREATE, "client_request_id": "e2-off-001"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["termination_reason"] == "dependency_create_host_denied"
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.get(ConversationPlan, cid) is None
            assert db.query(PlanToolDispatch).count() == 0
            assert db.query(AgentAttempt).count() == 0
