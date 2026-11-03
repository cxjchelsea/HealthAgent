"""Default real-provider Unified cutover contracts (model route stub only).

The endpoint runs with the actual automatic real-provider routing settings,
while the Primary model response is injected so network/model variability does
not affect the authoritative Host and execution regression.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal, PrimaryRouteRejected
from apps.api.main import create_app
from services.db import ConversationTask, database


def _settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'default_unified_primary.db'}",
        model_provider="ollama_proxy")


def _task():
    message = "请创建任务：提交周报"
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool", "quote": message,
        "execution_scope": "none", "effect_intent": "mutation_requested",
        "write_tool_name": "create_task",
        "write_arguments": {"title": "提交周报"},
    }), message, saved_plan_present=False, pending_action=None)


def _forbid(*args, **kwargs):
    raise AssertionError("default Unified must not invoke legacy semantic owner")


def test_auto_real_provider_selects_primary_without_explicit_canary(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    assert settings.routing_profile == "auto"
    assert settings.effective_routing_profile == "unified"
    assert settings.routing_profile_source == "auto_real_provider_default"
    assert settings.primary_route_v1_canary_enabled is False
    assert settings.primary_route_v1_active is True
    assert Settings(model_provider="fake").primary_route_v1_active is False
    assert Settings(model_provider="ollama_proxy", routing_profile="legacy").primary_route_v1_active is False
    assert Settings(model_provider="ollama_proxy", routing_profile="unified").primary_route_v1_active is True
    assert Settings(model_provider="fake", routing_profile="unified").primary_route_v1_active is False

    calls = []

    async def propose(*args, **kwargs):
        calls.append(kwargs)
        return _task()

    monkeypatch.setattr("apps.api.main.propose_primary_route", propose)
    for name in (
        "propose_new_plan_route", "propose_saved_plan_route",
        "review_plan_intent", "review_plan_cancellation",
        "classify_pending_reply", "review_plan_execution_scope",
        "reconcile_execution_policy", "decide_execution_policy_detail",
    ):
        monkeypatch.setattr("apps.api.main." + name, _forbid)

    with TestClient(create_app(settings)) as client:
        model = client.get("/model").json()
        assert model["primary_route_v1_active"] is True
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": "请创建任务：提交周报",
                  "client_request_id": "default-primary-write-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "primary_v1_task_created"
        assert response.json()["status"] == "completed"
        assert len(calls) == 1
        _, Session = database(settings.database_url)
        with Session() as db:
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            assert len(tasks) == 1
        replay = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": "请创建任务：提交周报",
                  "client_request_id": "default-primary-write-001"})
        assert replay.status_code == 409
        with Session() as db:
            assert len(db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()) == 1


def test_default_unified_invalid_primary_never_falls_back(tmp_path, monkeypatch):
    async def invalid(*args, **kwargs):
        raise PrimaryRouteRejected("primary_route_invalid_signal")
    monkeypatch.setattr("apps.api.main.propose_primary_route", invalid)
    for name in ("propose_new_plan_route", "propose_saved_plan_route",
                 "review_plan_intent", "decide_execution_policy_detail"):
        monkeypatch.setattr("apps.api.main." + name, _forbid)

    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": "请创建任务：提交周报",
                  "client_request_id": "default-primary-refused-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "primary_v1_proposal_refused"
        assert response.json()["steps"] == 0
        _, Session = database(settings.database_url)
        with Session() as db:
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []
