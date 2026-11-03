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
