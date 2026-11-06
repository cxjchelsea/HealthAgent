"""Fail-closed crash evidence for atomic create_task write in v1."""
import pytest
from sqlalchemy import select

from agent.primary_ordinary_write_v1 import execute_primary_task_create_v1
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from services.db import AgentAttempt, Conversation, ConversationTask, Message, Turn, database
from services.migrate import migrate
from services.task_store import ConversationTaskStore


def test_atomic_task_write_exception_does_not_retry(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'task_write_unknown.db'}"
    migrate(url)
    _, Session = database(url)
    message = "创建任务：提交测试报告"
    proposal = bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool", "quote": message,
        "execution_scope": "none", "effect_intent": "mutation_requested",
        "write_tool_name": "create_task",
        "write_arguments": {"title": "提交测试报告"},
    }), message, saved_plan_present=False, pending_action=None)

    with Session.begin() as db:
        conversation = Conversation(owner_id="test")
        db.add(conversation)
        db.flush()
        cid = conversation.id
        assistant = Message(conversation_id=cid, role="assistant",
                            content="", status="pending")
        db.add(assistant)
        db.flush()
        turn = Turn(conversation_id=cid, request_id="task-write-crash-001",
                    assistant_message_id=assistant.id, state="running")
        db.add(turn)
        db.flush()
        first_id = turn.id

    called = []
    def fail_inside_task_transaction(self, db, title):
        called.append(title)
        raise RuntimeError("write_transaction_rollback")

    monkeypatch.setattr(ConversationTaskStore, "create_in_session",
                        fail_inside_task_transaction)
    with pytest.raises(RuntimeError, match="write_transaction_rollback"):
        execute_primary_task_create_v1(
            proposal=proposal, session_factory=Session,
            conversation_id=cid, turn_id=first_id)

    with Session() as db:
        assert db.scalars(select(ConversationTask).where(
            ConversationTask.conversation_id == cid)).all() == []
        prepared = db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)).all()
        assert len(prepared) == 1 and prepared[0].status == "prepared"

    # Simulate an operator closing the failed Turn without inventing receipt.
    with Session.begin() as db:
        db.get(Turn, first_id).state = "partial"
        assistant = Message(conversation_id=cid, role="assistant",
                            content="", status="pending")
        db.add(assistant)
        db.flush()
        new_turn = Turn(conversation_id=cid, request_id="task-write-crash-002",
                        assistant_message_id=assistant.id, state="running")
        db.add(new_turn)
        db.flush()
        second_id = new_turn.id

    blocked = execute_primary_task_create_v1(
        proposal=proposal, session_factory=Session,
        conversation_id=cid, turn_id=second_id)
    assert blocked["termination_reason"] == "primary_v1_write_reconciliation_required"
    assert called == ["提交测试报告"]
    with Session() as db:
        assert db.scalars(select(ConversationTask).where(
            ConversationTask.conversation_id == cid)).all() == []
        assert len(db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)).all()) == 1
