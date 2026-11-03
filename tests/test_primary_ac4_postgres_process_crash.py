"""AC4 v1 local writes: real subprocess death and PostgreSQL read-back.

A fresh process is terminated by os._exit(73), bypassing Python cleanup,
for each of four exact effect boundaries. Parent uses a separate engine to
verify task, receipt, prepared evidence and one-shot authority states.
Never restarts an unknown write, alters a protected user conversation, or
asserts external exactly-once.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from services.db import (
    AgentAttempt, Conversation, ConversationTask, Message,
    PendingWriteAuthority, Turn, database,
)
from services.primary_pending_write import mint_pending_write_authority_in_session
from services.primary_write_reconciliation import (
    inspect_pending_write_authorities, inspect_primary_write_reconciliation,
)
from services.write_effect_fence import write_fence_in_session


CHILD = r"""
import os
import sys

from agent.primary_ordinary_write_v1 import execute_primary_write_v1
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from services.db import database
from services.primary_pending_write import consume_pending_task_write_v1
from services.task_store import ConversationTaskStore

url, cid, tid, origin_id, authority_id, mode = sys.argv[1:]
engine, Session = database(url)
title = "周五提交测试报告"
if mode.endswith("_inside"):
    original = ConversationTaskStore.create_in_session

    def crash_inside_transaction(self, db, task_title):
        result = original(self, db, task_title)
        assert result["ok"]
        db.flush()
        os._exit(73)

    ConversationTaskStore.create_in_session = crash_inside_transaction

if mode.startswith("ordinary"):
    message = "创建任务：周五提交测试报告"
    proposal = bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool", "quote": message,
        "execution_scope": "none", "effect_intent": "mutation_requested",
        "write_tool_name": "create_task",
        "write_arguments": {"title": title},
    }), message, saved_plan_present=False, pending_action=None)
    result = execute_primary_write_v1(
        proposal=proposal, session_factory=Session,
        conversation_id=cid, turn_id=tid)
else:
    result = consume_pending_task_write_v1(
        session_factory=Session, conversation_id=cid,
        current_turn_id=tid, origin_turn_id=origin_id,
        authority_id=authority_id, reply=title)
assert result["status"] == "completed", result
os._exit(73)
"""


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires physical TEST_POSTGRES_URL")
@pytest.mark.parametrize("mode", [
    "ordinary_inside", "ordinary_after", "pending_inside", "pending_after",
])
def test_real_postgres_process_crash_at_v1_local_write_boundaries(mode):
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql"), "physical crash verification requires PostgreSQL"
    engine, Session = database(url)
    with Session.begin() as db:
        conversation = Conversation(owner_id="ac4-v1-process-crash")
        db.add(conversation)
        db.flush()
        cid = conversation.id
        source = Message(conversation_id=cid, role="user",
                         content="帮我建任务，我还不知道标题。")
        clarification = Message(conversation_id=cid, role="assistant",
                                content="请补充标题", status="partial")
        db.add_all([source, clarification])
        db.flush()
        origin = Turn(conversation_id=cid, request_id="crash-origin-" + cid,
                      state="running", assistant_message_id=clarification.id)
        db.add(origin)
        db.flush()
        origin_id = origin.id
        authority_id = mint_pending_write_authority_in_session(
            db, conversation_id=cid, origin_turn_id=origin_id,
            origin_user_message_id=source.id, source_message=source.content,
            tool_name="create_task")
        origin.state = "partial"
        origin.failure_code = "needs_input:create_task"
    with Session.begin() as db:
        answer = Message(conversation_id=cid, role="assistant",
                         content="", status="pending")
        db.add(answer)
        db.flush()
        current = Turn(conversation_id=cid, request_id="crash-effect-" + cid,
                       state="running", assistant_message_id=answer.id)
        db.add(current)
        db.flush()
        turn_id = current.id
    engine.dispose()

    try:
        child = subprocess.run(
            [sys.executable, "-c", CHILD, url, cid, turn_id,
             origin_id, authority_id, mode],
            cwd=str(Path(__file__).parents[1]), timeout=30,
            capture_output=True, text=True)
        assert child.returncode == 73, (
            "child failed before expected crash boundary: " + child.stderr)

        engine, Session = database(url)
        with Session() as db:
            assert db.get(Turn, turn_id).state == "running"
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            attempts = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            permit = db.get(PendingWriteAuthority, authority_id)

            committed = mode.endswith("_after")
            assert len(tasks) == (1 if committed else 0), mode
            assert len(attempts) == (
                1 if committed or mode == "ordinary_inside" else 0), mode
            if attempts:
                assert attempts[0].status == (
                    "succeeded" if committed else "prepared"), mode
            assert permit.status == (
                "consumed" if mode == "pending_after" else "pending"), mode
            assert permit.consumed_turn_id == (
                turn_id if mode == "pending_after" else None), mode

        findings = inspect_primary_write_reconciliation(Session, cid)
        if mode == "ordinary_inside":
            assert findings[0]["assessment"] == "unknown_outcome"
        elif committed:
            assert findings[0]["assessment"] == "local_receipt_unfinalized"
        else:
            assert findings == []
        authority = inspect_pending_write_authorities(Session, cid)
        assert authority[0]["assessment"] == (
            "consumed_turn_unfinalized" if mode == "pending_after"
            else "awaiting_user_reply")

        with Session.begin() as db:
            blocked = write_fence_in_session(db, cid)
            assert (blocked == "write_reconciliation_required") == (
                mode != "pending_inside"), mode

        # Test never calls a tool again: unknown outcomes remain unresolved,
        # while a wholly rolled-back Pending transaction has no side effect
        # or consumption and is eligible for a *new authorized* attempt.
    finally:
        engine, Session = database(url)
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
