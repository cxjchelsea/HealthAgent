"""HTTP and effect-boundary acceptance for consented primary v1 memory writes."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import AgentAttempt, UserMemory, database


REQUEST = "请记住：键为QA_CODE，值为QA-17"


def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path / 'memory_v1.db'}",
                    model_provider="fake", routing_profile="unified",
                    primary_route_v1_canary_enabled=True)


def proposal():
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
        "quote": REQUEST, "execution_scope": "none",
        "effect_intent": "mutation_requested",
        "write_tool_name": "save_memory",
        "write_arguments": {"key": "QA_CODE", "value": "QA-17"},
    }), REQUEST, saved_plan_present=False, pending_action=None)


def test_memory_write_requires_current_turn_consent_and_atomic_receipt(
        tmp_path, monkeypatch):
    calls = []

    async def route(*args, **kwargs):
        calls.append(args[2])
        return proposal()

    def no_unbounded_loop(*args, **kwargs):
        raise AssertionError("memory write may not enter general AgentLoop")

    monkeypatch.setattr("apps.api.main.propose_primary_route", route)
    monkeypatch.setattr("apps.api.main.AgentLoop", no_unbounded_loop)
    cfg = settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        no_consent = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": REQUEST, "client_request_id": "v1-memory-denied-001",
            "memory_write_consent": False})
        assert no_consent.status_code == 200, no_consent.text
        assert no_consent.json()["termination_reason"] == "primary_v1_memory_consent_required"
        _, Session = database(cfg.database_url)
        with Session() as db:
            assert db.scalars(select(UserMemory)).all() == []
            assert db.scalars(select(AgentAttempt)).all() == []
        authorized = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": REQUEST, "client_request_id": "v1-memory-allowed-001",
            "memory_write_consent": True})
        assert authorized.status_code == 200, authorized.text
        assert authorized.json()["termination_reason"] == "primary_v1_memory_saved"
        assert authorized.json()["status"] == "completed"
        replay = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": REQUEST, "client_request_id": "v1-memory-allowed-001",
            "memory_write_consent": True})
        assert replay.status_code == 409
        with Session() as db:
            memories = db.scalars(select(UserMemory)).all()
            attempts = db.scalars(select(AgentAttempt)).all()
            assert len(memories) == len(attempts) == 1
            assert memories[0].key == "QA_CODE"
            assert memories[0].value == "QA-17"
            assert attempts[0].status == "succeeded"
            projection = json.loads(attempts[0].result_projection)
            assert projection == {"ok": True}
            assert "QA-17" not in attempts[0].result_projection
    assert len(calls) == 2


def test_memory_write_schema_rejects_unbound_key_or_value():
    from agent.primary_route_v1 import PrimaryRouteRejected
    import pytest
    bad = RouteSignal.model_validate({
        "schema_version": "primary-route-v1", "route_kind": "ordinary_tool",
        "quote": REQUEST, "execution_scope": "none",
        "effect_intent": "mutation_requested",
        "write_tool_name": "save_memory",
        "write_arguments": {"key": "QA_CODE", "value": "NOT_IN_REQUEST"},
    })
    with pytest.raises(PrimaryRouteRejected, match="primary_route_write_arguments_unbound"):
        bind_route_signal(bad, REQUEST, saved_plan_present=False,
                          pending_action=None)
