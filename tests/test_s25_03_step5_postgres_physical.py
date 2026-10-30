"""S25-03 Step 5 PostgreSQL physical execution-evidence boundary checks.

Uses isolated conversation IDs in dedicated TEST_POSTGRES_URL only. Exercises
the actual SQL transactions, not SQLite or mock DB calls.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from services.db import (AgentAttempt, AgentStateTransition, AgentTaskState,
                         Conversation, Message, PlanToolDispatch, Turn, database)
from services.evidence_store import AttemptEvidenceStore, make_attempt
from services.s25_03_projection import project_finalized_turn
from services.task_state import AgentTaskStateStore


def _setup():
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql"), "isolated PostgreSQL required"
    engine, Session = database(url)
    with Session.begin() as db:
        c = Conversation(owner_id="s25-03-step5-postgres-test")
        db.add(c)
        db.flush()
        cid = c.id
        m = Message(conversation_id=cid, role="assistant", content="",
                    status="pending")
        db.add(m)
        db.flush()
        t = Turn(conversation_id=cid, assistant_message_id=m.id,
                 request_id="s25-03-step5-" + cid, state="running")
        db.add(t)
        db.flush()
        tid = t.id
    return url, engine, Session, cid, tid


def _cleanup(Session, cid):
    with Session.begin() as db:
        db.execute(delete(AgentStateTransition).where(
            AgentStateTransition.conversation_id == cid))
        db.execute(delete(AgentTaskState).where(
            AgentTaskState.conversation_id == cid))
        db.execute(delete(AgentAttempt).where(AgentAttempt.conversation_id == cid))
        db.execute(delete(PlanToolDispatch).where(
            PlanToolDispatch.conversation_id == cid))
        db.execute(delete(Turn).where(Turn.conversation_id == cid))
        db.execute(delete(Message).where(Message.conversation_id == cid))
        c = db.get(Conversation, cid)
        if c is not None:
            db.delete(c)


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires isolated TEST_POSTGRES_URL")
def test_postgres_projection_binds_durable_m4_dispatch_and_transition():
    _, engine, Session, cid, tid = _setup()
    checkpoint = {"revision": 1, "steps": [{
        "id": 1, "status": "completed", "expected_tool": "calculate",
        "expected_arguments": {"expression": "7*6"}, "evidence_tool": "calculate"}]}
    try:
        with Session.begin() as db:
            db.add(PlanToolDispatch(
                conversation_id=cid, turn_id=tid, step_id=1,
                tool_name="calculate", arguments_json='{"expression":"7*6"}',
                status="checkpointed", receipt_json='{"ok":true,"value":"42"}'))
        with Session.begin() as db:
            project_finalized_turn(db, conversation_id=cid, turn_id=tid,
                                   result={"run_id": "postgres-real"},
                                   checkpoint=checkpoint, status="completed")
            db.get(Turn, tid).state = "completed"
        with Session() as db:
            dispatch = db.scalar(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid))
            attempt = db.get(AgentAttempt, dispatch.id)
            assert attempt is not None and attempt.status == "succeeded"
            assert attempt.turn_id == tid and attempt.step_id == 1
            assert db.scalar(select(AgentStateTransition.id).where(
                AgentStateTransition.conversation_id == cid,
                AgentStateTransition.attempt_id == dispatch.id,
                AgentStateTransition.reason_code == "verified_step_completed"))
            assert AgentTaskStateStore(cid).load(Session)["state"] == "completed"
    finally:
        _cleanup(Session, cid)
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires isolated TEST_POSTGRES_URL")
def test_postgres_crashed_ordinary_write_is_durable_and_unresolved():
    url, engine, Session, cid, tid = _setup()
    # Separate process commits prepared attempt, then dies immediately:
    # no tool dispatch, no receipt, no claim of external exactly once.
    script = """
import os,sys
from services.db import database
from services.evidence_store import AttemptEvidenceStore,make_attempt
engine,Session=database(sys.argv[1])
journal=AttemptEvidenceStore(sys.argv[2],sys.argv[3])
journal.begin(Session,make_attempt("crash","create_task",{"title":"x"},
                                  {"ok":False,"error":"prepared"}))
os._exit(79)
"""
    engine.dispose()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script, url, cid, tid],
            cwd=str(Path(__file__).resolve().parents[1]), timeout=30,
            capture_output=True, text=True)
        assert proc.returncode == 79, proc.stderr
        engine, Session = database(url)
        journal = AttemptEvidenceStore(cid, tid)
        unresolved = journal.unresolved(Session)
        assert len(unresolved) == 1
        assert unresolved[0]["status"] == "unknown_outcome"
        assert journal.has_unresolved_writes(Session)
        with Session() as db:
            attempt = db.get(AgentAttempt, unresolved[0]["attempt_id"])
            assert attempt.status == "prepared"
            assert attempt.result_projection is None
            assert db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)).all() == []
    finally:
        _cleanup(Session, cid)
        engine.dispose()
