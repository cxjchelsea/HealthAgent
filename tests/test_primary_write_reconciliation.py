"""No-auto-retry reconciliation inspection for durable local writes."""
import json

from services.db import (
    AgentAttempt, Conversation, ConversationTask, Message, Turn, database,
)
from services.migrate import migrate
from services.primary_write_reconciliation import inspect_primary_write_reconciliation


def test_reconciliation_is_read_only_and_requires_operator_for_unknown(tmp_path):
    url = f"sqlite:///{tmp_path / 'reconcile.db'}"
    migrate(url)
    _, Session = database(url)
    with Session.begin() as db:
        convo = Conversation(owner_id="test")
        db.add(convo)
        db.flush()
        cid = convo.id
        assistant = Message(conversation_id=cid, role="assistant",
                            content="", status="pending")
        db.add(assistant)
        db.flush()
        turn = Turn(conversation_id=cid, request_id="write-reconcile-001",
                    assistant_message_id=assistant.id, state="partial")
        db.add(turn)
        db.flush()
        db.add(AgentAttempt(
            id="attempt-unknown", conversation_id=cid,
            turn_id=turn.id, run_id=turn.id,
            tool_name="create_task", arguments_digest="x" * 64,
            status="prepared"))
        db.flush()

    first = inspect_primary_write_reconciliation(Session, cid)
    second = inspect_primary_write_reconciliation(Session, cid)
    assert first == second
    assert len(first) == 1
    assert first[0]["assessment"] == "unknown_outcome"
    assert first[0]["task_row_present"] is None
    # No automatic transition, additional attempt, or task creation.
    with Session() as db:
        from sqlalchemy import select
        assert len(db.scalars(select(AgentAttempt)).all()) == 1
        assert db.scalars(select(ConversationTask)).all() == []


def test_reconciliation_distinguishes_verified_receipt_from_turn_completion(tmp_path):
    url = f"sqlite:///{tmp_path / 'receipt.db'}"
    migrate(url)
    _, Session = database(url)
    with Session.begin() as db:
        convo = Conversation(owner_id="test")
        db.add(convo)
        db.flush()
        cid = convo.id
        assistant = Message(conversation_id=cid, role="assistant",
                            content="", status="pending")
        db.add(assistant)
        db.flush()
        turn = Turn(conversation_id=cid, request_id="write-reconcile-002",
                    assistant_message_id=assistant.id, state="partial")
        db.add(turn)
        db.flush()
        task = ConversationTask(id="task-reconcile-001",
                                conversation_id=cid, title="report")
        db.add(task)
        db.flush()
        db.add(AgentAttempt(
            id="attempt-receipt", conversation_id=cid,
            turn_id=turn.id, run_id=turn.id,
            tool_name="create_task", arguments_digest="y" * 64,
            status="succeeded",
            result_projection=json.dumps(
                {"ok": True, "task_id": task.id})))
        db.flush()
        turn_id = turn.id

    result = inspect_primary_write_reconciliation(Session, cid)
    assert result[0]["assessment"] == "local_receipt_unfinalized"
    assert result[0]["task_row_present"] is True
    with Session.begin() as db:
        db.get(Turn, turn_id).state = "completed"
    final = inspect_primary_write_reconciliation(Session, cid)
    assert final[0]["assessment"] == "verified_local_receipt"


def test_revoked_pending_authority_is_audited_as_terminal_not_conflict(tmp_path):
    """A valid Phase4-B revoke is not an unresolved write or an invalid state."""
    from sqlalchemy import select
    from services.db import PendingWriteAuthority
    from services.primary_pending_write import mint_pending_write_authority_in_session
    from services.primary_write_reconciliation import inspect_pending_write_authorities

    url = f"sqlite:///{tmp_path / 'revoke-audit.db'}"
    migrate(url)
    _, Session = database(url)
    with Session.begin() as db:
        convo = Conversation(owner_id="revoke-audit")
        db.add(convo)
        db.flush()
        cid = convo.id
        user = Message(conversation_id=cid, role="user",
                       content="请创建一个任务，但我没想好标题")
        assistant = Message(conversation_id=cid, role="assistant",
                            content="请补充标题", status="partial")
        db.add_all([user, assistant])
        db.flush()
        turn = Turn(conversation_id=cid, request_id="revoke-audit-origin",
                    state="running", assistant_message_id=assistant.id)
        db.add(turn)
        db.flush()
        authority_id = mint_pending_write_authority_in_session(
            db, conversation_id=cid, origin_turn_id=turn.id,
            origin_user_message_id=user.id, source_message=user.content,
            tool_name="create_task")
        turn.state = "partial"
        turn.failure_code = "needs_input:create_task"
    with Session.begin() as db:
        from services.primary_pending_write import revoke_pending_task_authority_in_session
        revoke_pending_task_authority_in_session(
            db, conversation_id=cid, origin_turn_id=turn.id,
            authority_id=authority_id)

    before = inspect_pending_write_authorities(Session, cid)
    assert len(before) == 1
    assert before[0]["assessment"] == "revoked_unconsumed"
    assert before[0]["consumed_turn_id"] is None
    assert inspect_pending_write_authorities(Session, cid) == before
    assert inspect_primary_write_reconciliation(Session, cid) == []
    with Session() as db:
        assert db.get(PendingWriteAuthority, authority_id).status == "revoked"
        assert db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)).all() == []
        assert db.scalars(select(ConversationTask).where(
            ConversationTask.conversation_id == cid)).all() == []


def test_revoked_pending_authority_with_consumed_turn_remains_conflict(tmp_path):
    """Malformed terminal authority must not be declared safely revoked."""
    from services.db import PendingWriteAuthority
    from services.primary_pending_write import mint_pending_write_authority_in_session
    from services.primary_write_reconciliation import inspect_pending_write_authorities

    url = f"sqlite:///{tmp_path / 'invalid-revoke-audit.db'}"
    migrate(url)
    _, Session = database(url)
    with Session.begin() as db:
        convo = Conversation(owner_id="invalid-revoke-audit")
        db.add(convo)
        db.flush()
        cid = convo.id
        user = Message(conversation_id=cid, role="user",
                       content="请创建一个任务，但我没想好标题")
        assistant = Message(conversation_id=cid, role="assistant",
                            content="请补充标题", status="partial")
        db.add_all([user, assistant])
        db.flush()
        origin = Turn(conversation_id=cid,
                      request_id="invalid-revoke-origin", state="running",
                      assistant_message_id=assistant.id)
        db.add(origin)
        db.flush()
        authority_id = mint_pending_write_authority_in_session(
            db, conversation_id=cid, origin_turn_id=origin.id,
            origin_user_message_id=user.id, source_message=user.content,
            tool_name="create_task")
        origin.state = "partial"
        origin.failure_code = "needs_input:create_task"
        permit = db.get(PendingWriteAuthority, authority_id)
        permit.status = "revoked"
        permit.consumed_turn_id = origin.id

    findings = inspect_pending_write_authorities(Session, cid)
    assert findings[0]["assessment"] == "authority_state_conflict"
