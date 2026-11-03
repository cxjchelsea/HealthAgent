"""Crash/unknown-outcome fence for journaled read-only Pending dispatch."""
import pytest
from sqlalchemy import select

from agent import primary_pending_readonly_v1 as pending
from services.db import AgentAttempt, Conversation, Message, Turn, database
from services.migrate import migrate


def test_pending_unknown_outcome_blocks_redispatch(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'pending_unknown.db'}"
    migrate(url)
    _, Session = database(url)
    with Session.begin() as db:
        conversation = Conversation(owner_id="test")
        db.add(conversation)
        db.flush()
        cid = conversation.id
        reply = Message(conversation_id=cid, role="assistant",
                        content="", status="pending")
        db.add(reply)
        db.flush()
        turn = Turn(conversation_id=cid, request_id="pending-crash-001",
                    assistant_message_id=reply.id, state="running")
        db.add(turn)
        db.flush()
        first_id = turn.id

    attempts = []
    def crash_after_dispatch(name, arguments, tasks, **kwargs):
        attempts.append((name, arguments))
        with Session() as db:
            prepared = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            assert len(prepared) == 1
            assert prepared[0].status == "prepared"
        raise RuntimeError("simulated_crash_after_dispatch")

    monkeypatch.setattr(pending, "execute_tool", crash_after_dispatch)
    with pytest.raises(RuntimeError, match="simulated_crash_after_dispatch"):
        pending.execute_pending_readonly_v1(
            message="MCP", conversation_id=cid,
            turn_id=first_id, session_factory=Session,
            prior_pending_action="mcp_reference_lookup",
            prior_origin_verified=True)

    with Session.begin() as db:
        previous = db.get(Turn, first_id)
        previous.state = "partial"
        next_reply = Message(conversation_id=cid, role="assistant",
                             content="", status="pending")
        db.add(next_reply)
        db.flush()
        next_turn = Turn(conversation_id=cid, request_id="pending-crash-002",
                         assistant_message_id=next_reply.id, state="running")
        db.add(next_turn)
        db.flush()
        second_id = next_turn.id

    blocked = pending.execute_pending_readonly_v1(
        message="MCP", conversation_id=cid,
        turn_id=second_id, session_factory=Session,
        prior_pending_action="mcp_reference_lookup",
        prior_origin_verified=True)
    assert blocked["termination_reason"] == "primary_v1_pending_reconciliation_required"
    assert attempts == [("mcp_reference_lookup", {"term": "MCP"})]
    with Session() as db:
        stored = db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)).all()
        assert len(stored) == 1
        assert stored[0].status == "prepared"
