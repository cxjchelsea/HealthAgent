"""Physical PostgreSQL CAS competition and durable Pending candidate lifecycle.

Runs under TEST_POSTGRES_URL, isolated synthetic conversations, independent
connections per worker; no mock in confirmation transaction.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select

from services.db import (
    AgentAttempt, Base, Conversation, ConversationTask, Message,
    PendingWriteAuthority, Turn, database,
)
from services.primary_pending_confirmation import (
    commit_confirmed_pending_task, reject_pending_task_candidate,
    stage_pending_task_candidate, replace_pending_task_candidate,
)
from services.primary_pending_write import mint_pending_write_authority_in_session

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="requires TEST_POSTGRES_URL")


def _prepared(Session):
    with Session.begin() as db:
        c = Conversation(owner_id="pending-confirmation-physical")
        db.add(c)
        db.flush()
        cid = c.id
        source = Message(conversation_id=cid, role="user",
                         content="创建一项虚构任务但标题待定")
        answer = Message(conversation_id=cid, role="assistant",
                         content="请提供任务标题", status="partial")
        db.add_all([source, answer])
        db.flush()
        origin = Turn(conversation_id=cid, request_id="origin-" + str(uuid.uuid4()),
                      state="running", assistant_message_id=answer.id)
        db.add(origin)
        db.flush()
        authority_id = mint_pending_write_authority_in_session(
            db, conversation_id=cid, origin_turn_id=origin.id,
            origin_user_message_id=source.id,
            source_message=source.content, tool_name="create_task")
        origin.state = "partial"
        origin.failure_code = "needs_input:create_task"
        origin_id = origin.id
    with Session.begin() as db:
        user = Message(conversation_id=cid, role="user",
                       content="核对虚构传感器登记表")
        assistant = Message(conversation_id=cid, role="assistant",
                            content="", status="pending")
        db.add_all([user, assistant])
        db.flush()
        candidate = Turn(conversation_id=cid, request_id="candidate-" + str(uuid.uuid4()),
                         state="running", assistant_message_id=assistant.id)
        db.add(candidate)
        db.flush()
        candidate_id = candidate.id
    staged = stage_pending_task_candidate(
        session_factory=Session, conversation_id=cid,
        current_turn_id=candidate_id, origin_turn_id=origin_id,
        authority_id=authority_id, reply="核对虚构传感器登记表")
    assert staged["termination_reason"] == "pending_write_confirmation_required"
    with Session.begin() as db:
        candidate = db.get(Turn, candidate_id)
        candidate.state = "partial"
        candidate.failure_code = "pending_write_confirmation_required"
        assistant = db.get(Message, candidate.assistant_message_id)
        assistant.status = "partial"
        assistant.content = staged["answer"]
    return cid, staged["confirmation"]


def _audit(Session, cid):
    with Session() as db:
        a = db.scalar(select(PendingWriteAuthority).where(
            PendingWriteAuthority.conversation_id == cid))
        tasks = db.scalars(select(ConversationTask).where(
            ConversationTask.conversation_id == cid)).all()
        attempts = db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)).all()
        return a.status, a.consumed_turn_id, len(tasks), len(attempts)


@pytest.mark.parametrize("competitor", ("confirm", "reject"))
def test_confirmation_concurrent_workers_single_winner(competitor):
    engine, Session = database(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    try:
        cid, token = _prepared(Session)
        barrier = threading.Barrier(2)

        def worker(op, n):
            barrier.wait(timeout=15)
            if op == "confirm":
                return commit_confirmed_pending_task(
                    session_factory=Session, conversation_id=cid,
                    authority_id=token["authority_id"],
                    confirmation_nonce=token["confirmation_nonce"],
                    candidate_digest=token["candidate_digest"],
                    request_id="physical-confirm-" + str(n))
            return reject_pending_task_candidate(
                session_factory=Session, conversation_id=cid,
                authority_id=token["authority_id"],
                confirmation_nonce=token["confirmation_nonce"],
                candidate_digest=token["candidate_digest"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(worker, "confirm", 1),
                     pool.submit(worker, competitor, 2)]
            results = [f.result(timeout=30) for f in tasks]

        successes = sum(x["status"] == "completed" for x in results)
        revokes = sum(x["status"] == "revoked" for x in results)
        state, consumed_turn_id, task_count, attempts = _audit(Session, cid)
        assert successes + revokes == 1, results
        assert task_count == attempts == successes
        assert state == ("consumed" if successes else "revoked")
        assert bool(consumed_turn_id) == bool(successes)
        duplicate = commit_confirmed_pending_task(
            session_factory=Session, conversation_id=cid,
            authority_id=token["authority_id"],
            confirmation_nonce=token["confirmation_nonce"],
            candidate_digest=token["candidate_digest"],
            request_id="physical-duplicate-" + str(uuid.uuid4()))
        assert duplicate["status"] == "denied"
        assert _audit(Session, cid)[2:] == (task_count, attempts)
    finally:
        engine.dispose()


def test_postgres_expiry_and_replacement_old_nonce_cannot_write():
    engine, Session = database(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    try:
        cid, token = _prepared(Session)
        replaced = replace_pending_task_candidate(
            session_factory=Session, conversation_id=cid,
            authority_id=token["authority_id"],
            confirmation_nonce=token["confirmation_nonce"],
            candidate_digest=token["candidate_digest"],
            new_title="检查虚构数据样本的记录")
        assert replaced["status"] == "awaiting_confirmation"
        rotated = replaced["confirmation"]
        assert rotated["confirmation_nonce"] != token["confirmation_nonce"]
        old = commit_confirmed_pending_task(
            session_factory=Session, conversation_id=cid,
            authority_id=token["authority_id"],
            confirmation_nonce=token["confirmation_nonce"],
            candidate_digest=token["candidate_digest"],
            request_id="old-" + str(uuid.uuid4()))
        assert old["status"] == "denied"
        with Session.begin() as db:
            auth = db.get(PendingWriteAuthority, token["authority_id"])
            auth.candidate_expires_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        expired = commit_confirmed_pending_task(
            session_factory=Session, conversation_id=cid,
            authority_id=rotated["authority_id"],
            confirmation_nonce=rotated["confirmation_nonce"],
            candidate_digest=rotated["candidate_digest"],
            request_id="expired-" + str(uuid.uuid4()))
        assert expired["reason"] == "confirmation_expired"
        assert _audit(Session, cid)[0] == "expired"
        assert _audit(Session, cid)[2:] == (0, 0)
    finally:
        engine.dispose()


def test_postgres_confirmation_transaction_crash_rolls_back_all(monkeypatch):
    from services.task_store import ConversationTaskStore

    engine, Session = database(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    try:
        cid, token = _prepared(Session)
        original_create = ConversationTaskStore.create_in_session

        def crash_after_insert(self, db, title):
            observed = original_create(self, db, title)
            assert observed["ok"]
            raise RuntimeError("synthetic_confirm_crash_after_task_insert")

        monkeypatch.setattr(ConversationTaskStore, "create_in_session",
                            crash_after_insert)
        with pytest.raises(RuntimeError, match="synthetic_confirm_crash"):
            commit_confirmed_pending_task(
                session_factory=Session, conversation_id=cid,
                authority_id=token["authority_id"],
                confirmation_nonce=token["confirmation_nonce"],
                candidate_digest=token["candidate_digest"],
                request_id="synthetic-confirm-crash")
        # The authority, task and attempt must be rolled back together.
        assert _audit(Session, cid)[0] == "awaiting_confirmation"
        assert _audit(Session, cid)[2:] == (0, 0)
        monkeypatch.setattr(ConversationTaskStore, "create_in_session",
                            original_create)
        result = commit_confirmed_pending_task(
            session_factory=Session, conversation_id=cid,
            authority_id=token["authority_id"],
            confirmation_nonce=token["confirmation_nonce"],
            candidate_digest=token["candidate_digest"],
            request_id="synthetic-confirm-recovery")
        assert result["status"] == "completed"
        assert _audit(Session, cid)[0] == "consumed"
        assert _audit(Session, cid)[2:] == (1, 1)
    finally:
        engine.dispose()


def test_postgres_confirmation_competes_with_new_chat_revoke():
    """Either confirmation precedes the chat, or the new chat revokes it.

    Both operations must serialize on the same Conversation parent row.
    Neither order can produce a task after candidate revocation.
    """
    from services.primary_pending_confirmation import (
        revoke_staged_candidates_on_new_turn,
    )
    engine, Session = database(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    try:
        cid, token = _prepared(Session)
        barrier = threading.Barrier(2)

        def confirm():
            barrier.wait(timeout=15)
            return commit_confirmed_pending_task(
                session_factory=Session, conversation_id=cid,
                authority_id=token["authority_id"],
                confirmation_nonce=token["confirmation_nonce"],
                candidate_digest=token["candidate_digest"],
                request_id="confirm-chat-race-" + str(uuid.uuid4()))

        def new_chat():
            barrier.wait(timeout=15)
            with Session.begin() as db:
                db.scalar(select(Conversation.id).where(
                    Conversation.id == cid).with_for_update())
                revoked = revoke_staged_candidates_on_new_turn(db, cid)
                answer = Message(conversation_id=cid, role="assistant",
                                 content="新聊天", status="completed")
                db.add(answer)
                db.flush()
                db.add(Turn(
                    conversation_id=cid,
                    request_id="chat-race-" + str(uuid.uuid4()),
                    assistant_message_id=answer.id, state="completed"))
                return revoked

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(confirm)
            second = pool.submit(new_chat)
            result = first.result(timeout=30)
            revoked = second.result(timeout=30)
        state, consumed_turn_id, tasks, attempts = _audit(Session, cid)
        assert tasks == attempts
        assert tasks in (0, 1)
        if tasks == 1:
            assert result["status"] == "completed"
            assert state == "consumed" and consumed_turn_id
            assert revoked == 0
        else:
            assert result["status"] == "denied"
            assert state == "revoked" and not consumed_turn_id
            assert revoked == 1
        retry = commit_confirmed_pending_task(
            session_factory=Session, conversation_id=cid,
            authority_id=token["authority_id"],
            confirmation_nonce=token["confirmation_nonce"],
            candidate_digest=token["candidate_digest"],
            request_id="chat-race-retry-" + str(uuid.uuid4()))
        assert retry["status"] == "denied"
        assert _audit(Session, cid)[2:] == (tasks, attempts)
    finally:
        engine.dispose()
