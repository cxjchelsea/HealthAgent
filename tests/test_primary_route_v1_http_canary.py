"""Opt-in v1 HTTP canary: one semantic owner and bounded Host operations.

These tests intentionally protect the legacy Unified default and prove that
unwired v1 intents are denied rather than silently reclassified or dispatched.
"""
import json

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import ConversationPlan, database


CREATE = "请创建一个两步可执行计划：第一步计算 3+4，第二步计算 8+9。只保存，不执行。"
CANCEL = "请取消当前保存的计划，不执行任何步骤。"


def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'v1_canary.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True)


def bound_create():
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "plan_create", "quote": "创建一个两步可执行计划",
        "execution_scope": "none", "effect_intent": "mutation_requested",
        "steps": [
            {"goal": "计算 3+4", "tool_name": "calculate",
             "arguments": {"expression": "3+4"}, "quote": "计算 3+4"},
            {"goal": "计算 8+9", "tool_name": "calculate",
             "arguments": {"expression": "8+9"}, "quote": "计算 8+9"},
        ],
    }), CREATE, saved_plan_present=False, pending_action=None)


def bound_cancel():
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "plan_cancel", "quote": "取消当前保存的计划",
        "execution_scope": "none", "effect_intent": "mutation_requested",
    }), CANCEL, saved_plan_present=True, pending_action=None)


def test_canary_create_cancel_uses_single_primary_and_host_cas(tmp_path, monkeypatch):
    calls = []

    async def route(*args, **kwargs):
        calls.append(kwargs["saved_plan_present"])
        return bound_cancel() if kwargs["saved_plan_present"] else bound_create()

    async def legacy_forbidden(*args, **kwargs):
        raise AssertionError("must never reach legacy semantic route")

    monkeypatch.setattr("apps.api.main.propose_primary_route", route)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", legacy_forbidden)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", legacy_forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", legacy_forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", legacy_forbidden)

    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        result = client.post(f"/conversations/{cid}/agent/messages",
                             json={"content": CREATE, "client_request_id": "v1-create-001"})
        assert result.status_code == 200, result.text
        assert result.json()["termination_reason"] == "plan_created_pending_execution"
        assert [s["status"] for s in result.json()["plan"]] == ["pending", "pending"]
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.get(ConversationPlan, cid) is not None
        cancelled = client.post(f"/conversations/{cid}/agent/messages",
                                json={"content": CANCEL, "client_request_id": "v1-cancel-001"})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["termination_reason"] == "plan_cancelled"
        with Session() as db:
            assert db.get(ConversationPlan, cid) is None
    assert calls == [False, True]


def test_canary_ordinary_read_only_preserves_host_mode(tmp_path, monkeypatch):
    calls = []

    async def ordinary(*args, **kwargs):
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
            "quote": "请计算", "execution_scope": "none",
            "effect_intent": "read_only",
        }), "请计算 5+2", saved_plan_present=False, pending_action=None)

    async def no_legacy(*args, **kwargs):
        raise AssertionError("ordinary v1 must not reclassify route")

    class Loop:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)

        async def run(self, *args, **kwargs):
            assert kwargs["execution_mode"] == "read_only"
            assert kwargs["plan_control"] is None
            assert kwargs["memory_write_consent"] is False
            assert kwargs["understanding_result"] is None
            return {"run_id": "fake", "status": "completed", "answer": "7",
                    "termination_reason": "model_final",
                    "trace": [{"step": 0, "type": "final"}], "steps": 0}

    monkeypatch.setattr("apps.api.main.propose_primary_route", ordinary)
    monkeypatch.setattr("apps.api.main.AgentLoop", Loop)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", no_legacy)
    monkeypatch.setattr("apps.api.main.decide_execution_policy_detail", no_legacy)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        result = client.post(f"/conversations/{cid}/agent/messages",
                             json={"content": "请计算 5+2", "client_request_id": "v1-ordinary-001"})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["ordinary_attempt_journal"] is not None


def test_canary_ordinary_write_remains_unwired(tmp_path, monkeypatch):
    async def mutation(*args, **kwargs):
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
            "quote": "创建任务", "execution_scope": "none",
            "effect_intent": "mutation_requested",
        }), "创建任务", saved_plan_present=False, pending_action=None)

    def forbidden(*args, **kwargs):
        raise AssertionError("mutation must not reach AgentLoop")

    monkeypatch.setattr("apps.api.main.propose_primary_route", mutation)
    monkeypatch.setattr("apps.api.main.AgentLoop", forbidden)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "创建任务", "client_request_id": "v1-mutation-001"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["termination_reason"] == "primary_v1_admission_not_wired"


def test_canary_invalid_primary_cannot_fall_back(tmp_path, monkeypatch):
    from agent.primary_route_v1 import PrimaryRouteRejected

    async def invalid(*args, **kwargs):
        raise PrimaryRouteRejected("primary_route_quote_ambiguous")

    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy after denial")

    monkeypatch.setattr("apps.api.main.propose_primary_route", invalid)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", forbidden)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages",
                           json={"content": CREATE, "client_request_id": "v1-invalid-001"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["termination_reason"] == "primary_v1_proposal_refused"
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.get(ConversationPlan, cid) is None


def test_canary_first_resume_through_m4_and_durable_plan(tmp_path, monkeypatch):
    """Create -> FIRST -> RESUME; every request uses one v1 proposal only."""
    calls = []

    async def route(*args, **kwargs):
        present = kwargs["saved_plan_present"]
        content = args[2]
        calls.append((content, present))
        if not present:
            return bound_create()
        if "第一步" in content:
            kind, scope, quote = "plan_execute_first", "first_step", "第一步"
        else:
            kind, scope, quote = "plan_resume", "remaining_steps", "剩余步骤"
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": kind, "quote": quote,
            "execution_scope": scope, "effect_intent": "read_only",
        }), content, saved_plan_present=True, pending_action=None)

    async def no_legacy(*args, **kwargs):
        raise AssertionError("v1 execution cannot call old semantic classifier")

    monkeypatch.setattr("apps.api.main.propose_primary_route", route)
    for name in ("propose_new_plan_route", "propose_saved_plan_route",
                 "review_plan_intent", "review_plan_execution_authorization",
                 "review_plan_execution_scope", "reconcile_execution_policy"):
        monkeypatch.setattr("apps.api.main." + name, no_legacy)

    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        def send(text, request_id):
            resp = client.post(f"/conversations/{cid}/agent/messages", json={
                "content": text, "client_request_id": request_id,
            })
            assert resp.status_code == 200, resp.text
            return resp.json()

        create = send(CREATE, "v1-execute-create-001")
        assert create["termination_reason"] == "plan_created_pending_execution"
        first = send("只执行已保存计划的第一步", "v1-execute-first-001")
        assert first["termination_reason"] == "pending_plan_steps"
        assert [s["status"] for s in first["plan"]] == ["completed", "pending"]
        assert [t["name"] for t in first["trace"] if t["type"] == "tool"] == ["calculate"]
        resume = send("继续执行剩余步骤", "v1-execute-resume-001")
        assert resume["termination_reason"] == "plan_execution_completed"
        assert [s["status"] for s in resume["plan"]] == ["completed", "completed"]
        assert [t["name"] for t in resume["trace"] if t["type"] == "tool"] == ["calculate"]
        assert len(calls) == 3
        _, Session = database(cfg.database_url)
        with Session() as db:
            saved = db.get(ConversationPlan, cid)
            assert saved is not None
            assert [s["status"] for s in json.loads(saved.snapshot)["steps"]] == [
                "completed", "completed"]


def test_canary_first_denies_missing_checkpoint_without_tools(tmp_path, monkeypatch):
    async def forged(*args, **kwargs):
        from agent.primary_route_v1 import PrimaryRouteRejected
        raise PrimaryRouteRejected("primary_route_missing_saved_plan")

    monkeypatch.setattr("apps.api.main.propose_primary_route", forged)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        resp = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "只执行第一步", "client_request_id": "v1-missing-first-001"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["termination_reason"] == "primary_v1_proposal_refused"
