"""HTTP regression for a model that mislabels task cancellation as title."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import AgentAttempt, ConversationTask, Message, PendingWriteAuthority, Turn, database


@pytest.mark.parametrize("cancel", [
    "算了，这个待办不创建了。",
    "取消刚才那条待办的创建请求，别替我新增任务。",
])
@pytest.mark.parametrize("cancel_route", ["clarify_response", "respond"])
def test_wrong_primary_pending_cancel_must_not_create_task(tmp_path, monkeypatch, cancel, cancel_route):
    origin = "我准备创建一个待办事项，但没有想好标题；请先询问标题。"
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
                    "route_kind": cancel_route, "quote": cancel}
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

    # This suite validates Primary admission + durable revoke, not natural-
    # language answer generation. Fake Provider cannot complete respond_only.
    class AnswerOnlyLoop:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, goal, **kwargs):
            assert kwargs["execution_mode"] == "respond_only"
            assert kwargs["memory_write_consent"] is False
            return {"run_id": "test-answer", "status": "completed",
                    "termination_reason": "model_final",
                    "answer": "已停止创建。", "trace": [], "steps": 0}

    if cancel_route == "respond":
        monkeypatch.setattr("apps.api.main.AgentLoop", AnswerOnlyLoop)
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
        if cancel_route == "clarify_response":
            assert second.json()["status"] == "partial"
            assert second.json()["termination_reason"] == "primary_v1_pending_task_withdrawn"
        else:
            assert second.json()["status"] in {"completed", "partial"}
            assert second.json()["termination_reason"] != "pending_task_created"
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
            assert authorities[0].status == "revoked"
            assert authorities[0].consumed_turn_id is None

        followup = client.post(endpoint, json={
            "content": unrelated, "client_request_id": "cancel-followup"})
        assert followup.status_code == 200, followup.text
        assert followup.json()["status"] == "partial"
        assert followup.json()["termination_reason"] == "primary_v1_missing_plan_status"
        with Session() as db:
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []

        # A caller holding old permit IDs must never reactivate withdrawn rights.
        from services.primary_pending_write import consume_pending_task_write_v1
        with Session.begin() as db:
            permit = db.scalar(select(PendingWriteAuthority).where(
                PendingWriteAuthority.conversation_id == cid))
            origin_id, permit_id = permit.origin_turn_id, permit.id
            assistant = Message(conversation_id=cid, role="assistant",
                                content="", status="pending")
            db.add(assistant)
            db.flush()
            synthetic = Turn(conversation_id=cid,
                             request_id="attempt-stale-revoked-permit",
                             assistant_message_id=assistant.id, state="running")
            db.add(synthetic)
            db.flush()
            stale_turn_id = synthetic.id
        replay = consume_pending_task_write_v1(
            session_factory=Session, conversation_id=cid,
            current_turn_id=stale_turn_id, origin_turn_id=origin_id,
            authority_id=permit_id, reply="模拟仪器复核")
        assert replay["termination_reason"] == "pending_write_authority_conflict"
        with Session() as db:
            assert db.get(PendingWriteAuthority, permit_id).status == "revoked"
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []
            assert db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all() == []


@pytest.mark.parametrize("route,followup", [
    ("respond", "换个话题，帮我概括一下合成测试项目。"),
    ("reject", "请执行一个不存在的外部命令。"),
    ("plan_missing", "继续执行此前保存的计算计划。"),
])
def test_abandoned_pending_write_is_revoked_after_non_continuation(
        tmp_path, monkeypatch, route, followup):
    """A fresh unrelated Turn must not leave an old one-use write permit alive."""
    origin = "我想创建一项待办，但标题没想好，请先问标题。"

    async def primary(*args, **kwargs):
        message = args[2]
        if message == origin:
            spec = {"schema_version": "primary-route-v1",
                    "route_kind": "clarify", "quote": origin,
                    "clarification_question": "请提供任务名称。",
                    "pending_write_tool_name": "create_task"}
        else:
            assert message == followup
            assert kwargs["pending_action"] == "create_task"
            spec = {"schema_version": "primary-route-v1",
                    "route_kind": route, "quote": followup}
        return bind_route_signal(
            RouteSignal.model_validate(spec), message,
            saved_plan_present=False, pending_action=kwargs["pending_action"],
            pending_origin_verified=kwargs["pending_origin_verified"])

    class AnswerOnlyLoop:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, goal, **kwargs):
            assert kwargs["execution_mode"] == "respond_only"
            return {"run_id": "test", "status": "completed",
                    "termination_reason": "model_final",
                    "answer": "项目概况。", "trace": [], "steps": 0}

    monkeypatch.setattr("apps.api.main.propose_primary_route", primary)
    if route == "respond":
        monkeypatch.setattr("apps.api.main.AgentLoop", AnswerOnlyLoop)

    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'abandoned.db'}",
                   model_provider="fake", routing_profile="unified",
                   primary_route_v1_canary_enabled=True, _env_file=None)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        first = client.post(endpoint, json={
            "content": origin, "client_request_id": "abandon-origin"})
        assert first.status_code == 200, first.text
        assert first.json()["termination_reason"] == "needs_input"

        other = client.post(endpoint, json={
            "content": followup, "client_request_id": "abandon-other"})
        assert other.status_code == 200, other.text
        assert other.json()["termination_reason"] != "pending_task_created"

        _, Session = database(cfg.database_url)
        with Session() as db:
            auth = db.scalars(select(PendingWriteAuthority).where(
                PendingWriteAuthority.conversation_id == cid)).all()
            assert len(auth) == 1
            assert auth[0].status == "revoked"
            assert auth[0].consumed_turn_id is None
            origin_id, authority_id = auth[0].origin_turn_id, auth[0].id
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []
            assert db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all() == []

        # Direct executor access with stale IDs must remain denied.
        from services.primary_pending_write import consume_pending_task_write_v1
        with Session.begin() as db:
            assistant = Message(conversation_id=cid, role="assistant",
                                content="", status="pending")
            db.add(assistant)
            db.flush()
            synthetic = Turn(conversation_id=cid,
                             request_id="abandon-stale-executor",
                             assistant_message_id=assistant.id, state="running")
            db.add(synthetic)
            db.flush()
            synthetic_turn_id = synthetic.id
        denied = consume_pending_task_write_v1(
            session_factory=Session, conversation_id=cid,
            current_turn_id=synthetic_turn_id,
            origin_turn_id=origin_id, authority_id=authority_id,
            reply="虚构测试任务")
        assert denied["termination_reason"] == "pending_write_authority_conflict"
        with Session() as db:
            assert db.get(PendingWriteAuthority, authority_id).status == "revoked"
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []
            assert db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all() == []
