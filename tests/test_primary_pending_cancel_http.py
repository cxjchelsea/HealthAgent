"""HTTP regression for a model that mislabels task cancellation as title."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import AgentAttempt, ConversationTask, PendingWriteAuthority, database


def test_wrong_primary_pending_cancel_must_not_create_task(tmp_path, monkeypatch):
    origin = "我准备创建一个待办事项，但没有想好标题；请先询问标题。"
    cancel = "算了，这个待办不创建了。"
    unrelated = "请继续执行之前存档的计算计划，别重新创建。"

    async def fake_primary(*args, **kwargs):
        message = args[2]
        if message == origin:
            spec = {"schema_version": "primary-route-v1", "route_kind": "clarify",
                    "quote": origin, "clarification_question": "请补充任务名称。",
                    "pending_write_tool_name": "create_task"}
        elif message == cancel:
            assert kwargs["pending_origin_verified"]
            assert kwargs["pending_action"] == "create_task"
            # Simulate real Qwen's incorrect successful-write semantic proposal.
            spec = {"schema_version": "primary-route-v1",
                    "route_kind": "clarify_response", "quote": cancel}
        else:
            assert message == unrelated
            assert kwargs["pending_action"] is None
            spec = {"schema_version": "primary-route-v1",
                    "route_kind": "plan_missing", "quote": unrelated}
        return bind_route_signal(
            RouteSignal.model_validate(spec), message,
            saved_plan_present=False,
            pending_action=kwargs["pending_action"],
            pending_origin_verified=kwargs["pending_origin_verified"])

    monkeypatch.setattr("apps.api.main.propose_primary_route", fake_primary)
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'cancel_guard.db'}",
                   model_provider="fake", routing_profile="unified",
                   primary_route_v1_canary_enabled=True)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        first = client.post(endpoint, json={
            "content": origin, "client_request_id": "cancel-origin"})
        assert first.status_code == 200, first.text
        assert first.json()["termination_reason"] == "needs_input"

        second = client.post(endpoint, json={
            "content": cancel, "client_request_id": "cancel-reply"})
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "partial"
        assert second.json()["termination_reason"] == "primary_v1_pending_task_withdrawn"
        assert not any(event.get("name") == "create_task"
                       for event in second.json().get("trace", []))

        _, Session = database(cfg.database_url)
        with Session() as db:
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            authorities = db.scalars(select(PendingWriteAuthority).where(
                PendingWriteAuthority.conversation_id == cid)).all()
            assert tasks == []
            assert db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all() == []
            assert len(authorities) == 1
            assert authorities[0].status == "pending"

        followup = client.post(endpoint, json={
            "content": unrelated, "client_request_id": "cancel-followup"})
        assert followup.status_code == 200, followup.text
        assert followup.json()["status"] == "partial"
        assert followup.json()["termination_reason"] == "primary_v1_missing_plan_status"
        with Session() as db:
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []
