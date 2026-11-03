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


def test_canary_unwired_ordinary_no_legacy_or_tool(tmp_path, monkeypatch):
    async def ordinary(*args, **kwargs):
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
            "quote": "请计算", "execution_scope": "none",
            "effect_intent": "read_only",
        }), "请计算 5+2", saved_plan_present=False, pending_action=None)

    def forbidden(*args, **kwargs):
        raise AssertionError("unwired route cannot invoke AgentLoop")

    monkeypatch.setattr("apps.api.main.propose_primary_route", ordinary)
    monkeypatch.setattr("apps.api.main.AgentLoop", forbidden)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        result = client.post(f"/conversations/{cid}/agent/messages",
                             json={"content": "请计算 5+2", "client_request_id": "v1-ordinary-001"})
        assert result.status_code == 200, result.text
        obj = result.json()
        assert obj["termination_reason"] == "primary_v1_admission_not_wired"
        assert not any(t["type"] == "tool" for t in obj["trace"])


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
