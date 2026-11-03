"""PostgreSQL competition and durable Pending write recovery evidence.

Runs against the CI Postgres service via TEST_POSTGRES_URL. Two independent
database sessions compete for the SAME origin authority. There may only be
one running Turn per conversation, so both workers exercise the same Turn
identity directly; public HTTP rejects duplicate concurrent Turns earlier.
"""
import os
import threading
import time

import pytest
from sqlalchemy import delete, select

from services.db import (
    AgentAttempt, Base, Conversation, ConversationTask, Message,
    PendingWriteAuthority, Turn, database,
)
from services.primary_pending_write import (
    consume_pending_task_write_v1, mint_pending_write_authority_in_session,
)
from services.primary_write_reconciliation import (
    inspect_pending_write_authorities,
)
from services.task_store import ConversationTaskStore


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires TEST_POSTGRES_URL")
def test_postgres_pending_authority_competing_workers_and_unfinalized_audit(
        monkeypatch):
    engine, Session = database(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        conversation = Conversation(owner_id="primary-pending-pg-test")
        db.add(conversation)
        db.flush()
        cid = conversation.id
        original_user = Message(
            conversation_id=cid, role="user",
            content="请创建任务，但我暂时没有标题。")
        original_answer = Message(
            conversation_id=cid, role="assistant", content="请提供任务标题",
            status="partial")
        db.add_all([original_user, original_answer])
        db.flush()
        origin = Turn(conversation_id=cid, request_id="pg-origin-001",
                      state="running",
                      assistant_message_id=original_answer.id)
        db.add(origin)
        db.flush()
        origin_id = origin.id
        auth_id = mint_pending_write_authority_in_session(
            db, conversation_id=cid, origin_turn_id=origin_id,
            origin_user_message_id=original_user.id,
            source_message=original_user.content, tool_name="create_task")
        origin.state = "partial"
        origin.failure_code = "needs_input:create_task"

    with Session.begin() as db:
        reply_user = Message(conversation_id=cid, role="user",
                             content="周五提交测试报告")
        reply_assistant = Message(conversation_id=cid, role="assistant",
                                  content="", status="pending")
        db.add_all([reply_user, reply_assistant])
        db.flush()
        current = Turn(conversation_id=cid, request_id="pg-reply-001",
                       assistant_message_id=reply_assistant.id, state="running")
        db.add(current)
        db.flush()
        current_id = current.id

    entered = threading.Event()
    release = threading.Event()
    observations = []
    original_create = ConversationTaskStore.create_in_session

    def block_first_create(self, db, title):
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=15), "first transaction not released"
        return original_create(self, db, title)

    monkeypatch.setattr(ConversationTaskStore, "create_in_session",
                        block_first_create)

    def work(name):
        try:
            result = consume_pending_task_write_v1(
                session_factory=Session, conversation_id=cid,
                current_turn_id=current_id, origin_turn_id=origin_id,
                authority_id=auth_id, reply="周五提交测试报告")
            observations.append((name, result["termination_reason"]))
        except Exception as exc:
            observations.append((name, type(exc).__name__ + ":" + str(exc)))

    first = threading.Thread(target=work, args=("first",), daemon=True)
    second = threading.Thread(target=work, args=("second",), daemon=True)
    try:
        first.start()
        assert entered.wait(timeout=15), "first worker did not acquire CAS"
        second.start()
        time.sleep(0.2)
    finally:
        release.set()
    first.join(timeout=15)
    second.join(timeout=15)
    try:
        assert not first.is_alive() and not second.is_alive(), observations
        assert len(observations) == 2
        assert sorted(x[1] for x in observations).count("pending_task_created") == 1, observations
        assert len([x for x in observations if x[1] in {
            "pending_write_authority_conflict",
            "pending_write_already_consumed",
            "pending_write_reconciliation_required"}]) == 1, observations
        with Session() as db:
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            attempts = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            permit = db.get(PendingWriteAuthority, auth_id)
            assert len(tasks) == len(attempts) == 1
            assert permit.status == "consumed"
            assert permit.consumed_turn_id == current_id
            assert attempts[0].status == "succeeded"
        audit = inspect_pending_write_authorities(Session, cid)
        assert audit[0]["assessment"] == "consumed_turn_unfinalized"
        with Session.begin() as db:
            db.get(Turn, current_id).state = "completed"
        assert inspect_pending_write_authorities(
            Session, cid)[0]["assessment"] == "consumed_completed"
    finally:
        # Scoped teardown; never truncate shared CI database.
        with Session.begin() as db:
            db.execute(delete(AgentAttempt).where(
                AgentAttempt.conversation_id == cid))
            db.execute(delete(ConversationTask).where(
                ConversationTask.conversation_id == cid))
            db.execute(delete(PendingWriteAuthority).where(
                PendingWriteAuthority.conversation_id == cid))
            db.execute(delete(Turn).where(Turn.conversation_id == cid))
            db.execute(delete(Message).where(Message.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
