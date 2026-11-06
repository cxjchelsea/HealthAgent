"""AC4 PostgreSQL cross-entry write governance, not just same-entry CAS.

The ordinary write and Pending continuation must contend for the same
conversation-level lock. Exactly one local effect and receipt may commit.
The second entry must not mistake the first entry's incomplete Turn for a
new execution authorization.
"""
import os
import threading
import time

import pytest
from sqlalchemy import delete, select

from agent.primary_ordinary_write_v1 import execute_primary_write_v1
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from services.db import (
    AgentAttempt, Base, Conversation, ConversationTask, Message,
    PendingWriteAuthority, Turn, database,
)
from services.evidence_store import AttemptEvidenceStore
from services.primary_pending_write import (
    consume_pending_task_write_v1, mint_pending_write_authority_in_session,
)
from services.task_store import ConversationTaskStore


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires TEST_POSTGRES_URL")
def test_cross_entry_ordinary_and_pending_write_are_serialized(monkeypatch):
    engine, Session = database(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    origin_text = "请创建一个任务，但暂时没有标题"
    title = "周五提交测试报告"
    ordinary_text = "创建任务：周五提交测试报告"
    proposal = bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool", "quote": ordinary_text,
        "effect_intent": "mutation_requested", "execution_scope": "none",
        "write_tool_name": "create_task", "write_arguments": {"title": title},
    }), ordinary_text, saved_plan_present=False, pending_action=None)
    with Session.begin() as db:
        convo = Conversation(owner_id="ac4-cross-entry")
        db.add(convo)
        db.flush()
        cid = convo.id
        original_user = Message(conversation_id=cid, role="user",
                                content=origin_text)
        original_assistant = Message(conversation_id=cid, role="assistant",
                                     content="请提供标题", status="partial")
        db.add_all([original_user, original_assistant])
        db.flush()
        origin_turn = Turn(conversation_id=cid,
                           request_id="cross-entry-origin-001",
                           assistant_message_id=original_assistant.id,
                           state="running")
        db.add(origin_turn)
        db.flush()
        origin_id = origin_turn.id
        authority_id = mint_pending_write_authority_in_session(
            db, conversation_id=cid, origin_turn_id=origin_id,
            origin_user_message_id=original_user.id,
            source_message=origin_text, tool_name="create_task")
        origin_turn.state = "partial"
        origin_turn.failure_code = "needs_input:create_task"
    with Session.begin() as db:
        current_assistant = Message(conversation_id=cid, role="assistant",
                                    content="", status="pending")
        db.add(current_assistant)
        db.flush()
        current = Turn(conversation_id=cid, request_id="cross-entry-current-001",
                       assistant_message_id=current_assistant.id,
                       state="running")
        db.add(current)
        db.flush()
        current_id = current.id

    effect_entered = threading.Event()
    release_effect = threading.Event()
    results = {}
    original_create = ConversationTaskStore.create_in_session

    def delayed_create(self, db, task_title):
        if threading.current_thread().name == "ordinary-write":
            effect_entered.set()
            assert release_effect.wait(timeout=15), "ordinary writer not released"
        return original_create(self, db, task_title)

    monkeypatch.setattr(ConversationTaskStore, "create_in_session", delayed_create)

    def ordinary():
        try:
            result = execute_primary_write_v1(
                proposal=proposal, session_factory=Session,
                conversation_id=cid, turn_id=current_id)
            results["ordinary"] = result["termination_reason"]
        except Exception as exc:
            results["ordinary_error"] = type(exc).__name__ + ":" + str(exc)

    def pending():
        try:
            result = consume_pending_task_write_v1(
                session_factory=Session, conversation_id=cid,
                current_turn_id=current_id, origin_turn_id=origin_id,
                authority_id=authority_id, reply=title)
            results["pending"] = result["termination_reason"]
        except Exception as exc:
            results["pending_error"] = type(exc).__name__ + ":" + str(exc)

    t1 = threading.Thread(target=ordinary, name="ordinary-write", daemon=True)
    t2 = threading.Thread(target=pending, name="pending-write", daemon=True)
    try:
        t1.start()
        assert effect_entered.wait(timeout=15), "ordinary write did not begin"
        t2.start()
        time.sleep(0.2)
    finally:
        release_effect.set()
    t1.join(timeout=15)
    t2.join(timeout=15)
    try:
        assert not t1.is_alive() and not t2.is_alive(), results
        assert results.get("ordinary") == "primary_v1_task_created", results
        assert results.get("pending") == "pending_write_reconciliation_required", results
        with Session() as db:
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            receipts = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            assert len(tasks) == len(receipts) == 1
            assert receipts[0].status == "succeeded"
            assert db.get(PendingWriteAuthority, authority_id).status == "pending"
        assert AttemptEvidenceStore(cid, current_id).has_unresolved_writes(Session)
    finally:
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
