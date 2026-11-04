"""Physical HTTP contract for Host-minted one-time Pending task write authority.

No second semantic classification, no legacy pending write permission, and
no task until original Turn is committed and source-bound authority consumed.
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from apps.api.main import create_app
from services.db import (
    AgentAttempt, ConversationTask, Message, PendingWriteAuthority, Turn, database,
)

ORIGIN = "请帮我创建一个任务，但是我还没想好任务标题。"
REPLY = "提交周五测试报告"


def cfg(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path / 'pending_authority.db'}",
                    model_provider="fake", routing_profile="unified",
                    primary_route_v1_canary_enabled=True)


def make_clarification():
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1", "route_kind": "clarify",
        "quote": "创建一个任务", "execution_scope": "none",
        "effect_intent": "none",
        "clarification_question": "请提供要创建的任务名称。",
        "pending_write_tool_name": "create_task",
    }), ORIGIN, saved_plan_present=False, pending_action=None)


def make_reply():
    return bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1", "route_kind": "clarify_response",
        "quote": REPLY, "execution_scope": "none", "effect_intent": "none",
    }), REPLY, saved_plan_present=False, pending_action="create_task",
        pending_origin_verified=True)


def test_pending_task_authority_full_http_cycle_and_replay(tmp_path, monkeypatch):
    recorded = []

    async def route(*args, **kwargs):
        recorded.append((args[2], kwargs["pending_origin_verified"],
                         kwargs["pending_action"]))
        if args[2] == ORIGIN:
            return make_clarification()
        assert kwargs["pending_action"] == "create_task"
        assert kwargs["pending_origin_verified"] is True
        return make_reply()

    async def forbidden(*args, **kwargs):
        raise AssertionError("legacy second classifier invoked")

    def no_loop(*args, **kwargs):
        raise AssertionError("pending write cannot enter AgentLoop")

    monkeypatch.setattr("apps.api.main.propose_primary_route", route)
    monkeypatch.setattr("apps.api.main.classify_pending_reply", forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", forbidden)
    monkeypatch.setattr("apps.api.main.AgentLoop", no_loop)
    settings = cfg(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        path = f"/conversations/{cid}/agent/messages"
        first = client.post(path, json={
            "content": ORIGIN, "client_request_id": "v1-pending-origin-001"})
        assert first.status_code == 200, first.text
        assert first.json()["termination_reason"] == "needs_input"
        assert first.json()["status"] == "partial"
        _, Session = database(settings.database_url)
        with Session() as db:
            authority = db.scalar(select(PendingWriteAuthority).where(
                PendingWriteAuthority.conversation_id == cid))
            assert authority is not None
            assert authority.status == "pending"
            origin_turn = db.get(Turn, authority.origin_turn_id)
            assert origin_turn.state == "partial"
            assert origin_turn.failure_code == "needs_input:create_task"
            source = db.get(Message, authority.origin_user_message_id)
            assert source.content == ORIGIN
            assert db.scalars(select(ConversationTask)).all() == []

        sent = {"content": REPLY, "client_request_id": "v1-pending-answer-001"}
        second = client.post(path, json=sent)
        assert second.status_code == 200, second.text
        assert second.json()["termination_reason"] == "pending_task_created"
        assert second.json()["status"] == "completed"
        assert client.post(path, json=sent).status_code == 409
        with Session() as db:
            authority = db.scalar(select(PendingWriteAuthority).where(
                PendingWriteAuthority.conversation_id == cid))
            assert authority.status == "consumed"
            assert db.get(Turn, authority.consumed_turn_id).state == "completed"
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            attempts = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            assert len(tasks) == len(attempts) == 1
            assert tasks[0].title == REPLY
            assert attempts[0].status == "succeeded"
            assert json.loads(attempts[0].result_projection)["task_id"] == tasks[0].id
        assert [p[0] for p in recorded] == [ORIGIN, REPLY]


def test_old_pending_failure_code_cannot_mint_authority(tmp_path, monkeypatch):
    """A historical needs_input is not proof of a new v1 write permit."""
    settings = cfg(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            old = Message(conversation_id=cid, role="assistant",
                          content="请提供任务标题", status="partial")
            db.add(old)
            db.flush()
            db.add(Turn(conversation_id=cid, request_id="old-pending-001",
                        state="partial", assistant_message_id=old.id,
                        failure_code="needs_input:create_task"))
        async def denied(*args, **kwargs):
            assert kwargs["pending_origin_verified"] is False
            from agent.primary_route_v1 import PrimaryRouteRejected
            raise PrimaryRouteRejected("primary_route_untrusted_pending_origin")
        monkeypatch.setattr("apps.api.main.propose_primary_route", denied)
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": REPLY, "client_request_id": "old-pending-answer-001"})
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "primary_v1_proposal_refused"
        with Session() as db:
            assert db.scalars(select(PendingWriteAuthority)).all() == []
            assert db.scalars(select(ConversationTask)).all() == []


def test_pending_write_malformed_title_is_not_consumed(tmp_path, monkeypatch):
    from services.primary_pending_write import (
        consume_pending_task_write_v1, mint_pending_write_authority_in_session,
    )
    settings = cfg(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            source = Message(conversation_id=cid, role="user", content=ORIGIN)
            answer = Message(conversation_id=cid, role="assistant",
                             content="请提供任务标题", status="partial")
            db.add_all([source, answer])
            db.flush()
            turn = Turn(conversation_id=cid, request_id="direct-origin-001",
                        state="running", assistant_message_id=answer.id)
            db.add(turn)
            db.flush()
            origin_id = turn.id
            permit_id = mint_pending_write_authority_in_session(
                db, conversation_id=cid, origin_turn_id=turn.id,
                origin_user_message_id=source.id, source_message=ORIGIN,
                tool_name="create_task")
            turn.state = "partial"
            turn.failure_code = "needs_input:create_task"
        with Session.begin() as db:
            assistant = Message(conversation_id=cid, role="assistant",
                                content="", status="pending")
            db.add(assistant)
            db.flush()
            turn2 = Turn(conversation_id=cid, request_id="direct-reply-001",
                         state="running", assistant_message_id=assistant.id)
            db.add(turn2)
            db.flush()
            new_id = turn2.id
        rejected = consume_pending_task_write_v1(
            session_factory=Session, conversation_id=cid,
            current_turn_id=new_id, origin_turn_id=origin_id,
            authority_id=permit_id, reply=" " * 2)
        assert rejected["termination_reason"] == "pending_write_invalid_title"
        with Session() as db:
            assert db.get(PendingWriteAuthority, permit_id).status == "pending"
            assert db.scalars(select(ConversationTask)).all() == []


def test_pending_write_transaction_rolls_back_authority_and_effect(tmp_path, monkeypatch):
    import pytest
    from services.primary_pending_write import (
        consume_pending_task_write_v1, mint_pending_write_authority_in_session,
    )
    from services.task_store import ConversationTaskStore
    settings = cfg(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            source = Message(conversation_id=cid, role="user", content=ORIGIN)
            assistant = Message(conversation_id=cid, role="assistant",
                                content="请提供任务标题", status="partial")
            db.add_all([source, assistant])
            db.flush()
            original = Turn(conversation_id=cid, request_id="rollback-origin-001",
                            state="running", assistant_message_id=assistant.id)
            db.add(original)
            db.flush()
            origin_id = original.id
            authority_id = mint_pending_write_authority_in_session(
                db, conversation_id=cid, origin_turn_id=origin_id,
                origin_user_message_id=source.id,
                source_message=ORIGIN, tool_name="create_task")
            original.state = "partial"
            original.failure_code = "needs_input:create_task"
        with Session.begin() as db:
            assistant = Message(conversation_id=cid, role="assistant",
                                content="", status="pending")
            db.add(assistant)
            db.flush()
            current = Turn(conversation_id=cid, request_id="rollback-reply-001",
                           state="running", assistant_message_id=assistant.id)
            db.add(current)
            db.flush()
            current_id = current.id

        original_create = ConversationTaskStore.create_in_session

        def crash(self, db, title):
            # Crash after task INSERT but before authorization/receipt COMMIT.
            original_create(self, db, title)
            raise RuntimeError("crash_in_local_transaction")

        monkeypatch.setattr(ConversationTaskStore, "create_in_session", crash)
        with pytest.raises(RuntimeError, match="crash_in_local_transaction"):
            consume_pending_task_write_v1(
                session_factory=Session, conversation_id=cid,
                current_turn_id=current_id, origin_turn_id=origin_id,
                authority_id=authority_id, reply=REPLY)

        with Session() as db:
            assert db.get(PendingWriteAuthority, authority_id).status == "pending"
            assert db.scalars(select(ConversationTask)).all() == []
            assert db.scalars(select(AgentAttempt)).all() == []

        # Transaction rollback proves there is no local side effect. A later
        # explicitly authorized attempt can retry the SAME permit safely.
        monkeypatch.setattr(ConversationTaskStore, "create_in_session",
                            original_create)
        restored = consume_pending_task_write_v1(
            session_factory=Session, conversation_id=cid,
            current_turn_id=current_id, origin_turn_id=origin_id,
            authority_id=authority_id, reply=REPLY)
        assert restored["termination_reason"] == "pending_task_created"
        with Session() as db:
            assert db.get(PendingWriteAuthority, authority_id).status == "consumed"
            assert len(db.scalars(select(ConversationTask)).all()) == 1
            assert len(db.scalars(select(AgentAttempt)).all()) == 1


def test_consumed_authority_cannot_dispatch_again(tmp_path):
    from services.primary_pending_write import (
        consume_pending_task_write_v1, mint_pending_write_authority_in_session,
    )
    settings = cfg(tmp_path)
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            source = Message(conversation_id=cid, role="user", content=ORIGIN)
            assistant = Message(conversation_id=cid, role="assistant",
                                content="请提供任务标题", status="partial")
            db.add_all([source, assistant])
            db.flush()
            original = Turn(conversation_id=cid, request_id="reuse-origin-001",
                            state="running", assistant_message_id=assistant.id)
            db.add(original)
            db.flush()
            origin_id = original.id
            permit = mint_pending_write_authority_in_session(
                db, conversation_id=cid, origin_turn_id=origin_id,
                origin_user_message_id=source.id,
                source_message=ORIGIN, tool_name="create_task")
            original.state = "partial"
            original.failure_code = "needs_input:create_task"
        with Session.begin() as db:
            assistant = Message(conversation_id=cid, role="assistant",
                                content="", status="pending")
            db.add(assistant)
            db.flush()
            current = Turn(conversation_id=cid, request_id="reuse-reply-001",
                           state="running", assistant_message_id=assistant.id)
            db.add(current)
            db.flush()
            first_id = current.id
        created = consume_pending_task_write_v1(
            session_factory=Session, conversation_id=cid,
            current_turn_id=first_id, origin_turn_id=origin_id,
            authority_id=permit, reply=REPLY)
        assert created["termination_reason"] == "pending_task_created"
        with Session.begin() as db:
            db.get(Turn, first_id).state = "completed"
            assistant = Message(conversation_id=cid, role="assistant",
                                content="", status="pending")
            db.add(assistant)
            db.flush()
            next_turn = Turn(conversation_id=cid, request_id="reuse-reply-002",
                             state="running", assistant_message_id=assistant.id)
            db.add(next_turn)
            db.flush()
            next_id = next_turn.id
        replay = consume_pending_task_write_v1(
            session_factory=Session, conversation_id=cid,
            current_turn_id=next_id, origin_turn_id=origin_id,
            authority_id=permit, reply=REPLY)
        assert replay["termination_reason"] == "pending_write_authority_conflict"
        with Session() as db:
            assert len(db.scalars(select(ConversationTask)).all()) == 1
            assert len(db.scalars(select(AgentAttempt)).all()) == 1


def test_pending_direct_executor_rejects_compound_topic_switch_before_db_access():
    """A direct caller cannot use the stale permit to bypass the Host shape fence."""
    from services.primary_pending_write import consume_pending_task_write_v1

    class ForbiddenDatabase:
        @staticmethod
        def begin():
            raise AssertionError("ambiguous Pending write must not access DB")

    denied = consume_pending_task_write_v1(
        session_factory=ForbiddenDatabase,
        conversation_id="synthetic-conversation",
        current_turn_id="synthetic-current",
        origin_turn_id="synthetic-original",
        authority_id="synthetic-permit",
        reply="我们换个话题，简单介绍一下模型上下文协议是什么。")
    assert denied["status"] == "partial"
    assert denied["termination_reason"] == "pending_write_title_not_standalone"
    assert not any(t.get("type") == "tool" for t in denied["trace"])
