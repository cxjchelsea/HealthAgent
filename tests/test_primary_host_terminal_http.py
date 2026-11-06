"""AC3 integration: a Host restriction cannot fall through to dispatch.

These HTTP tests exercise the same Primary v1 branch used by the unified
convergence work and deliberately make the downstream executor fatal.
"""
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.primary_host_admission_v1 import PrimaryHostDecision
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import ConversationTask, database
from sqlalchemy import select


def _settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'host_gate.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True)


def _bound_task():
    message = "请创建任务：提交周报"
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool",
        "quote": message,
        "execution_scope": "none",
        "effect_intent": "mutation_requested",
        "write_tool_name": "create_task",
        "write_arguments": {"title": "提交周报"},
    }), message, saved_plan_present=False, pending_action=None)


def _forbidden(*args, **kwargs):
    raise AssertionError("Host non-allow must not dispatch or invoke legacy")


def test_host_restrict_is_terminal_before_authorized_write(tmp_path, monkeypatch):
    async def primary(*args, **kwargs):
        return _bound_task()

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.admit_primary_route_v1",
                        lambda *args, **kwargs: PrimaryHostDecision(
                            disposition="restrict",
                            reason="host_scope_restricted",
                            route_kind="ordinary_tool"))
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1", _forbidden)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", _forbidden)
    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", _forbidden)
    with TestClient(create_app(_settings(tmp_path))) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages",
                            json={"content": "请创建任务：提交周报",
                                  "client_request_id": "host-restrict-001"})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert body["status"] == "partial"
        assert body["termination_reason"] == "host_scope_restricted"
        assert body["steps"] == 0
        _, Session = database(_settings(tmp_path).database_url)
        with Session() as db:
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []


def test_host_clarify_for_write_cannot_execute(tmp_path, monkeypatch):
    async def primary(*args, **kwargs):
        return _bound_task()

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    monkeypatch.setattr("apps.api.main.admit_primary_route_v1",
                        lambda *args, **kwargs: PrimaryHostDecision(
                            disposition="clarify",
                            reason="host_needs_clarification",
                            route_kind="ordinary_tool"))
    monkeypatch.setattr("apps.api.main.execute_primary_write_v1", _forbidden)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", _forbidden)
    with TestClient(create_app(_settings(tmp_path))) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages",
                            json={"content": "请创建任务：提交周报",
                                  "client_request_id": "host-clarify-001"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["termination_reason"] == "host_needs_clarification"
        assert reply.json()["steps"] == 0
