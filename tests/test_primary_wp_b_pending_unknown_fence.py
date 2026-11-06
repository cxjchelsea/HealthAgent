"""WP-B AC4: pending MCP unknown dispatch must not silently replay.

The pending executor is deliberately exercised without mock journal logic;
the first external call crashes after the prepared record is committed, and
the subsequent attempt must be blocked by the durable unresolved fence.
"""
from sqlalchemy import select

import pytest

from agent.primary_pending_readonly_v1 import execute_pending_readonly_v1
from services.db import (
    AgentAttempt, Base, Conversation, Message, Turn, database,
)


def test_pending_mcp_crash_leaves_prepared_and_blocks_next_dispatch(
        tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'pending_unknown.db'}"
    engine, Session = database(url)
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        conversation = Conversation(owner_id="wp-b-test")
        db.add(conversation)
        db.flush()
        cid = conversation.id
        reply = Message(conversation_id=cid, role="assistant",
                        content="", status="pending")
        db.add(reply)
        db.flush()
        turn = Turn(conversation_id=cid, request_id="pending-unknown-001",
                    assistant_message_id=reply.id, state="running")
        db.add(turn)
        db.flush()
        turn_id = turn.id

    tool_calls = []
    def crash_after_dispatch(*args, **kwargs):
        tool_calls.append(args[0])
        raise RuntimeError("simulated_tool_crash")

    monkeypatch.setattr("agent.primary_pending_readonly_v1.execute_tool",
                        crash_after_dispatch)
    with pytest.raises(RuntimeError, match="simulated_tool_crash"):
        execute_pending_readonly_v1(
            message="MCP", conversation_id=cid, turn_id=turn_id,
            session_factory=Session, prior_pending_action="mcp_reference_lookup",
            prior_origin_verified=True)

    with Session() as db:
        records = db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == cid)).all()
        assert len(records) == 1
        assert records[0].status == "prepared"
        assert records[0].tool_name == "mcp_reference_lookup"
        assert records[0].result_projection is None
    denied = execute_pending_readonly_v1(
        message="MCP", conversation_id=cid, turn_id=turn_id,
        session_factory=Session, prior_pending_action="mcp_reference_lookup",
        prior_origin_verified=True)
    assert denied["termination_reason"] == (
        "primary_v1_pending_reconciliation_required")
    assert denied["steps"] == 0
    assert tool_calls == ["mcp_reference_lookup"]
    with Session() as db:
        assert len(db.scalars(select(AgentAttempt)).all()) == 1


def test_pending_mcp_invalid_origin_never_prepares_attempt(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'pending_origin.db'}"
    engine, Session = database(url)
    Base.metadata.create_all(engine)
    def forbidden(*args, **kwargs):
        raise AssertionError("untrusted pending origin cannot dispatch")
    monkeypatch.setattr("agent.primary_pending_readonly_v1.execute_tool",
                        forbidden)
    denied = execute_pending_readonly_v1(
        message="MCP", conversation_id="no-conversation",
        turn_id="no-turn", session_factory=Session,
        prior_pending_action="mcp_reference_lookup",
        prior_origin_verified=False)
    assert denied["termination_reason"] == "primary_v1_pending_origin_denied"
    assert denied["steps"] == 0
    with Session() as db:
        assert db.scalars(select(AgentAttempt)).all() == []
