"""S25-06 Slice D gated HTTP integration: one Primary owner, no legacy projection.

These tests seed a valid saved new-format plan through the DB. Creating
such a plan from a model is explicitly not implemented by Slice D.
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import (
    AgentAttempt, AgentStateTransition, ConversationPlan, PlanExecutionClaim,
    PlanToolDispatch, Turn, database,
)


FIRST = "请只执行已保存计划的第一步"
RESUME = "请继续执行已保存计划的剩余步骤"


def _plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询 RAG 定义", "status": "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0", "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "统计定义文本", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0", "argument_bindings": {
                 "text": {"kind": "observation_ref",
                          "source_step_id": 1, "field": "definition"}}},
        ],
        "executions": {},
    }


def _settings(tmp_path, *, enabled=True):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 's25_06_d_http.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=enabled,
    )


def _seed(Session, cid):
    with Session.begin() as db:
        db.add(ConversationPlan(
            conversation_id=cid,
            snapshot=json.dumps(_plan(), ensure_ascii=False)))


def _route(text):
    if "第一步" in text:
        kind, scope, quote = "plan_execute_first", "first_step", "第一步"
    else:
        kind, scope, quote = "plan_resume", "remaining_steps", "剩余步骤"
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": kind, "quote": quote,
        "execution_scope": scope, "effect_intent": "read_only",
    }), text, saved_plan_present=True, pending_action=None, saved_step_count=2)


def test_http_new_format_first_then_resume_with_no_legacy_projection(tmp_path, monkeypatch):
    seen = []

    async def primary(*args, **kwargs):
        seen.append(args[2])
        return _route(args[2])

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    cfg = _settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        _seed(Session, cid)
        first = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": FIRST, "client_request_id": "s2506-http-first-001"})
        assert first.status_code == 200, first.text
        assert first.json()["termination_reason"] == "dependency_steps_checkpointed"
        with Session() as db:
            plan = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert [s["status"] for s in plan["steps"]] == ["completed", "pending"]
            assert db.get(PlanExecutionClaim, cid) is None
            assert db.query(AgentAttempt).count() == 1
            assert db.query(AgentStateTransition).filter_by(
                reason_code="verified_step_completed").count() == 1
            assert db.get(Turn, first.json()["turn_id"]).state == "completed"
        resume = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": RESUME, "client_request_id": "s2506-http-resume-001"})
        assert resume.status_code == 200, resume.text
        assert resume.json()["termination_reason"] == "dependency_steps_checkpointed"
        with Session() as db:
            saved = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert all(s["status"] == "completed" for s in saved["steps"])
            dispatches = db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid).order_by(
                PlanToolDispatch.step_id)).all()
            assert len(dispatches) == 2
            assert all(d.status == "checkpointed" for d in dispatches)
            assert db.query(AgentAttempt).count() == 2
            assert db.query(AgentStateTransition).filter_by(
                reason_code="verified_step_completed").count() == 2
            assert db.get(PlanExecutionClaim, cid) is None
            assert db.get(Turn, resume.json()["turn_id"]).state == "completed"
    assert seen == [FIRST, RESUME]


def test_http_disabled_new_plan_never_dispatches_or_reclassifies(tmp_path, monkeypatch):
    async def primary(*args, **kwargs):
        return _route(args[2])

    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy semantic classifier must not run")

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", forbidden)
    cfg = _settings(tmp_path, enabled=False)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        _seed(Session, cid)
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": RESUME, "client_request_id": "s2506-http-disabled-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "dependency_host_disabled"
        with Session() as db:
            assert db.query(PlanToolDispatch).count() == 0
            assert json.loads(db.get(ConversationPlan, cid).snapshot) == _plan()


def test_http_unreceipted_dispatch_does_not_replay(tmp_path, monkeypatch):
    async def primary(*args, **kwargs):
        return _route(args[2])

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    from agent.tool_registry import READ_REGISTRY
    def crash(*args, **kwargs):
        raise RuntimeError("synthetic crash after M4 dispatch")

    monkeypatch.setattr(READ_REGISTRY, "invoke", crash)
    cfg = _settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        _seed(Session, cid)
        first = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": RESUME, "client_request_id": "s2506-http-crash-001"})
        assert first.status_code == 503, first.text
        with Session() as db:
            dispatches = db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)).all()
            assert len(dispatches) == 1
            assert dispatches[0].status == "dispatch_unknown"
            assert db.query(AgentAttempt).count() == 0
            assert db.get(PlanExecutionClaim, cid) is None
            assert json.loads(db.get(ConversationPlan, cid).snapshot) == _plan()
        second = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": RESUME, "client_request_id": "s2506-http-crash-002"})
        assert second.status_code in {200, 503}, second.text
        with Session() as db:
            assert db.query(PlanToolDispatch).count() == 1
            assert db.query(AgentAttempt).count() == 0
