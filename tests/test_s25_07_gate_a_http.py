"""Gate A HTTP contract: negative Receipt != unknown and never retries."""
import json

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from agent.tool_registry import READ_REGISTRY
from apps.api.main import create_app
from services.db import (
    AgentAttempt, AgentTaskState, ConversationPlan, PlanToolDispatch, database,
)


def test_http_verified_failure_is_reported_without_replan_or_replay(tmp_path, monkeypatch):
    request = "请继续执行已保存计划的剩余步骤"
    async def primary(*args, **kwargs):
        return bind_route_signal(RouteSignal.model_validate({
            "schema_version": "primary-route-v1",
            "route_kind": "plan_resume", "quote": "剩余步骤",
            "execution_scope": "remaining_steps", "effect_intent": "read_only",
        }), args[2], saved_plan_present=True, pending_action=None,
            saved_step_count=2)
    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    calls = []
    def fail(name, *args, **kwargs):
        calls.append(name)
        return {"ok": False, "error": "controlled_read_failure"}
    monkeypatch.setattr(READ_REGISTRY, "invoke", fail)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'gate_a_http.db'}",
        model_provider="fake", routing_profile="unified",
        primary_route_v1_canary_enabled=True,
        s25_06_dependency_plan_enabled=True,
        s25_07_failure_evidence_enabled=True)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        from test_s25_07_gate_a_failure_evidence import _plan
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            db.add(ConversationPlan(
                conversation_id=cid,
                snapshot=json.dumps(_plan(), ensure_ascii=False)))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": request, "client_request_id": "gate-a-failure-001"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "partial"
        assert response.json()["termination_reason"] == (
            "dependency_verified_failure_pending_replan")
        assert calls == ["mcp_reference_lookup"]
        with Session() as db:
            dispatch = db.query(PlanToolDispatch).one()
            assert dispatch.status == "failure_finalized"
            assert db.get(AgentAttempt, dispatch.id).status == "failed"
            assert db.get(AgentTaskState, cid).state == "blocked"
            assert json.loads(db.get(ConversationPlan, cid).snapshot) == _plan()
        second = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": request, "client_request_id": "gate-a-failure-002"})
        assert second.status_code in (200, 503), second.text
        assert calls == ["mcp_reference_lookup"]
        with Session() as db:
            assert db.query(PlanToolDispatch).count() == 1
