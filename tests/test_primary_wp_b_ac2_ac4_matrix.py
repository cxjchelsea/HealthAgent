"""WP-B AC2/AC4: exhaustive non-effectful primary route/Host matrix.

Uses actual HTTP Turn, durable Primary evidence and Host. The semantic model
is deterministic; every retired reviewer fails if called. Live model
generalization is a separate AC5 requirement.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import (
    PrimaryRouteRejected, RouteSignal, bind_route_signal,
)
from apps.api.main import create_app
from services.db import AgentAttempt, ConversationPlan, ConversationTask, database

RETIRED = (
    "review_plan_cancellation", "propose_first_route",
    "review_plan_intent", "check_plan_edit_intent",
    "classify_pending_reply", "review_pending_reference_lookup",
    "review_unsupported_capability", "review_read_only_clarification",
    "reconcile_execution_policy", "review_plan_creation_reference",
    "review_missing_plan_reference", "review_plan_execution_authorization",
    "review_plan_execution_scope",
    "propose_new_plan_route", "propose_saved_plan_route",
    "review_plan_revision", "decide_execution_policy_detail",
)


def _forbidden(*args, **kwargs):
    raise AssertionError("WP-B: forbidden legacy semantic owner or effect")


CASE = {
    "absent_plan": ("请继续已保存计划", "plan_missing",
                    "primary_v1_missing_plan_status", "restrict"),
    "reject": ("请执行未注册命令", "reject",
               "primary_v1_route_rejected", "restrict"),
    "clarify": ("请帮我处理这个不明确的请求", "clarify",
                "primary_v1_clarification_required", "clarify"),
    "respond": ("给我一句项目描述", "respond", None, "allow"),
    "readonly": ("请查询资料", "ordinary_tool", None, "allow"),
    "memory_denied": ("请记住偏好为绿色", "ordinary_tool",
                      "primary_v1_memory_consent_required", "deny"),
    "invalid_schema": ("请执行某个任务", None,
                       "primary_v1_proposal_refused", "refused"),
    "unbound_quote": ("请建立计划", None,
                      "primary_v1_proposal_refused", "refused"),
}


@pytest.mark.parametrize("routing_profile", ["auto", "unified"])
@pytest.mark.parametrize("scenario", list(CASE))
def test_ac2_ac4_non_effectful_routes_have_no_legacy_or_mutations(
        tmp_path, monkeypatch, routing_profile, scenario):
    message, kind, reason, disposition = CASE[scenario]
    called = []
    for name in RETIRED:
        monkeypatch.setattr("apps.api.main." + name, _forbidden)
    monkeypatch.setattr("apps.api.main.UnderstandingService", _forbidden)
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1", _forbidden)
    monkeypatch.setattr("apps.api.main.consume_pending_task_write_v1", _forbidden)
    monkeypatch.setattr("apps.api.main.execute_pending_readonly_v1", _forbidden)

    async def proposal(*args, **kwargs):
        called.append(args[2])
        assert args[2] == message
        if scenario == "invalid_schema":
            raise PrimaryRouteRejected("primary_route_invalid_signal")
        if scenario == "unbound_quote":
            raise PrimaryRouteRejected("primary_route_quote_not_found")
        signal = {
            "schema_version": "primary-route-v1",
            "route_kind": kind,
            "quote": message,
            "execution_scope": "none",
            "effect_intent": "none",
        }
        if kind == "clarify":
            signal["clarification_question"] = "请明确操作目标。"
        if kind == "ordinary_tool":
            if scenario == "memory_denied":
                signal.update({
                    "effect_intent": "mutation_requested",
                    "write_tool_name": "save_memory",
                    "write_arguments": {"key": "偏好", "value": "绿色"},
                })
            else:
                signal["effect_intent"] = "read_only"
        return bind_route_signal(
            RouteSignal.model_validate(signal), message,
            saved_plan_present=False, pending_action=None)

    class NoToolLoop:
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, goal, **kwargs):
            assert kwargs["execution_mode"] == (
                "respond_only" if scenario == "respond" else "read_only")
            assert kwargs["plan_control"] is None
            assert kwargs["memory_write_consent"] is False
            return {"run_id": "wp-b", "status": "completed",
                    "answer": "没有进行任何持久化变更",
                    "trace": [], "steps": 0}

    monkeypatch.setattr("apps.api.main.propose_primary_route", proposal)
    monkeypatch.setattr("apps.api.main.AgentLoop", NoToolLoop)
    cfg = Settings(
        database_url=f"sqlite:///{tmp_path / (scenario + '-' + routing_profile + '.db')}",
        model_provider="ollama_proxy", routing_profile=routing_profile)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        data = {"content": message, "client_request_id": "wp-b-" + scenario}
        resp = client.post(f"/conversations/{cid}/agent/messages", json=data)
        assert resp.status_code == 200, resp.text
        result = resp.json()
        if reason is None:
            assert result["status"] == "completed"
        else:
            assert result["termination_reason"] == reason
            assert result["steps"] == 0
        assert len(called) == 1
        diag = client.get(
            f"/conversations/{cid}/turns/{result['turn_id']}/diagnostics")
        assert diag.status_code == 200
        audit = diag.json()["primary_route_evidence"]
        assert audit["disposition"] == disposition
        assert audit["route_kind"] == kind
        assert audit["semantic_attempts"] == 0  # model proposal injected
        assert audit["proposal_id"]
        replay = client.post(f"/conversations/{cid}/agent/messages", json=data)
        assert replay.status_code == 409
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.scalars(select(ConversationTask)).all() == []
            assert db.scalars(select(AgentAttempt)).all() == []
            assert db.get(ConversationPlan, cid) is None


@pytest.mark.parametrize("routing_profile", ["auto", "unified"])
def test_ac2_host_restrict_is_terminal_with_durable_proposal(
        tmp_path, monkeypatch, routing_profile):
    from agent.primary_host_admission_v1 import PrimaryHostDecision

    for name in RETIRED:
        monkeypatch.setattr("apps.api.main." + name, _forbidden)
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1", _forbidden)
    monkeypatch.setattr("apps.api.main.AgentLoop", _forbidden)
    msg = "请创建任务：提交周报"
    async def proposal(*args, **kwargs):
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "ordinary_tool",
            "quote": msg, "execution_scope": "none",
            "effect_intent": "mutation_requested",
            "write_tool_name": "create_task",
            "write_arguments": {"title": "提交周报"},
        }), msg, saved_plan_present=False, pending_action=None)
    monkeypatch.setattr("apps.api.main.propose_primary_route", proposal)
    monkeypatch.setattr("apps.api.main.admit_primary_route_v1",
                        lambda *args, **kwargs: PrimaryHostDecision(
                            "restrict", "wp_b_host_restricted", "ordinary_tool"))
    cfg = Settings(
        database_url=f"sqlite:///{tmp_path / 'restrict.db'}",
        model_provider="ollama_proxy", routing_profile=routing_profile)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": msg, "client_request_id": "wp-b-host-restrict"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["termination_reason"] == "wp_b_host_restricted"
        diag = client.get(
            f"/conversations/{cid}/turns/{reply.json()['turn_id']}/diagnostics")
        assert diag.json()["primary_route_evidence"]["disposition"] == "restrict"
        assert diag.json()["primary_route_evidence"]["route_kind"] == "ordinary_tool"
        with database(cfg.database_url)[1]() as db:
            assert db.scalars(select(ConversationTask)).all() == []
            assert db.scalars(select(AgentAttempt)).all() == []
