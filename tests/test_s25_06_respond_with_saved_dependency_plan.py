"""Regression: answer-only topic switch must not parse a versioned plan as legacy.

Actual HTTP -> Primary Host admission -> real AgentLoop.run and finalization.
The only mocked pieces are the external Primary/LLM decisions. Durable saved
plan and tool/claim invariants are read from the actual isolated DB.
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.decision import Decision
from agent.planning import PlanLedger
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import (
    AgentAttempt, ConversationPlan, PlanExecutionClaim, PlanToolDispatch,
    PrimaryRouteEvidence, Turn, database,
)


def _dependency_plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1,
        "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询 RAG", "depends_on": [],
             "status": "pending", "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {"term": {
                 "kind": "literal", "value": "RAG", "source_quote": "RAG"}}},
            {"id": 2, "goal": "统计 definition", "depends_on": [1],
             "status": "pending", "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {"text": {
                 "kind": "observation_ref", "source_step_id": 1,
                 "field": "definition"}}},
        ],
        "executions": {},
    }


def _cfg(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 's25-06-respond-topic-switch.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True,
    )


def test_http_topic_switch_respond_preserves_saved_dependency_without_legacy_restore(
        tmp_path, monkeypatch):
    plan = _dependency_plan()
    # Reproduce the reported root cause explicitly, not just a mocked
    # outcome: the old PlanLedger cannot restore the new dependency shape.
    import pytest
    with pytest.raises(ValueError, match="invalid_plan_binding"):
        PlanLedger.restore(plan)

    seen = []

    async def primary(provider, model, message, **kwargs):
        assert kwargs["saved_plan_present"] is True
        assert kwargs["dependency_plan_enabled"] is False
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "respond",
            "quote": message,
            "execution_scope": "none", "effect_intent": "none",
        }), message, saved_plan_present=True, pending_action=None,
            saved_step_count=2)

    class AnswerOnlyModel:
        def __init__(self, settings):
            pass

        async def decide(self, messages, tools):
            seen.append([t["function"]["name"] for t in tools])
            return Decision(content="Transformer 注意力机制通过 Q/K/V 计算相关性。", tool_calls=[])

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.NativeToolDecision", AnswerOnlyModel)

    settings = _cfg(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            db.add(ConversationPlan(
                conversation_id=cid,
                snapshot=json.dumps(plan, ensure_ascii=False)))
        with Session() as db:
            original_raw = db.get(ConversationPlan, cid).snapshot

        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "先不要执行刚才的计划。现在请解释 Transformer 的注意力机制。",
            "client_request_id": "s2506-respond-topic-switch-001",
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "completed"
        assert result["termination_reason"] == "model_final"
        assert result["execution_mode"] == "respond_only"
        assert "Q/K/V" in result["answer"]
        assert seen == [[]], "respond_only must not expose any tools"
        with Session() as db:
            assert db.get(ConversationPlan, cid).snapshot == original_raw
            assert db.get(PlanExecutionClaim, cid) is None
            assert db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)).all() == []
            assert db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all() == []
            turn = db.get(Turn, result["turn_id"])
            assert turn.state == "completed"
            evidence = db.get(PrimaryRouteEvidence, result["turn_id"])
            assert evidence.route_kind == "respond"
            assert evidence.disposition == "allow"
            assert evidence.admission_reason == "primary_v1_answer_only"


def test_legacy_plan_still_uses_legacy_restore_when_answer_only(tmp_path, monkeypatch):
    """Do not discard the checkpoint of an existing old-format plan."""
    legacy = PlanLedger()
    assert legacy.propose(["先查询资料"])["ok"] is True
    snapshot = legacy.checkpoint()
    seen = []

    async def primary(provider, model, message, **kwargs):
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "respond", "quote": message,
            "execution_scope": "none", "effect_intent": "none",
        }), message, saved_plan_present=True, pending_action=None,
            saved_step_count=1)

    from agent.loop import AgentLoop
    original_run = AgentLoop.run

    async def recorded_run(self, goal, **kwargs):
        seen.append(kwargs.get("plan_checkpoint"))
        return await original_run(self, goal, **kwargs)

    class AnswerOnlyModel:
        def __init__(self, settings):
            pass
        async def decide(self, messages, tools):
            return Decision(content="只回答问题。", tool_calls=[])

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.NativeToolDecision", AnswerOnlyModel)
    monkeypatch.setattr("agent.loop.AgentLoop.run", recorded_run)
    settings = _cfg(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            db.add(ConversationPlan(
                conversation_id=cid,
                snapshot=json.dumps(snapshot, ensure_ascii=False)))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请解释一下注意力机制。",
            "client_request_id": "s2506-respond-legacy-preserve-001",
        })
        assert response.status_code == 200, response.text
        # Preserve the legacy behavior too: an unfinished old plan can keep
        # an unrelated response partial, but must not lose its checkpoint.
        assert response.json()["status"] == "partial"
        assert response.json()["termination_reason"] == "pending_plan_steps"
        assert seen and seen[0] == snapshot


def test_http_ordinary_read_with_saved_dependency_skips_legacy_ledger(
        tmp_path, monkeypatch):
    """A normal Host-admitted read is equally independent from saved dependency plan."""
    message = "与刚才的计划无关，请用计算工具计算 19+23。"

    async def primary(provider, model, message, **kwargs):
        assert kwargs["saved_plan_present"] is True
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "ordinary_tool",
            "quote": message,
            "execution_scope": "none",
            "effect_intent": "read_only",
        }), message, saved_plan_present=True, pending_action=None,
            saved_step_count=2)

    class NoToolModel:
        def __init__(self, settings):
            pass

        async def decide(self, messages, tools):
            # An admitted ordinary read must fail closed without real
            # observation. This deliberately challenges ledger isolation,
            # rather than claiming the tool has successfully executed.
            return Decision(content="42", tool_calls=[])

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.NativeToolDecision", NoToolModel)
    settings = _cfg(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        snapshot = json.dumps(_dependency_plan(), ensure_ascii=False)
        with Session.begin() as db:
            db.add(ConversationPlan(conversation_id=cid, snapshot=snapshot))

        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": message,
            "client_request_id": "s2506-ordinary-read-topic-switch-001",
        })
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "partial"
        assert result["termination_reason"] == "ordinary_read_unverified"
        assert result["execution_mode"] == "read_only"
        with Session() as db:
            assert db.get(ConversationPlan, cid).snapshot == snapshot
            assert db.get(PlanExecutionClaim, cid) is None
            assert db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)).all() == []
            assert db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all() == []
            primary_evidence = db.get(PrimaryRouteEvidence, result["turn_id"])
            assert primary_evidence.route_kind == "ordinary_tool"
            assert primary_evidence.disposition == "allow"
            assert primary_evidence.admission_reason == "primary_v1_ordinary_read_only"
