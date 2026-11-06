"""PostgreSQL race on the same immutable Primary Turn admission.

Every worker uses its own DB transaction; the unique Turn key ensures that a
retry cannot replace the first admitted proposal, even under concurrency.
"""
import os
import threading

import pytest
from sqlalchemy import delete, select

from agent.primary_host_admission_v1 import PrimaryHostDecision
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from services.db import (
    Base, Conversation, Message, PrimaryRouteEvidence, Turn, database,
)
from services.primary_route_evidence import (
    record_primary_admission, snapshot_digest,
)


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires TEST_POSTGRES_URL")
def test_postgres_one_primary_admission_per_turn_under_competition():
    engine, Session = database(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        c = Conversation(owner_id="wp-a-primary-evidence-pg")
        db.add(c)
        db.flush()
        cid = c.id
        m = Message(conversation_id=cid, role="assistant",
                    content="", status="pending")
        db.add(m)
        db.flush()
        turn = Turn(conversation_id=cid, request_id="wp-a-primary-race-001",
                    assistant_message_id=m.id, state="running")
        db.add(turn)
        db.flush()
        tid = turn.id

    message = "请创建任务：提交周报"
    proposal = bind_route_signal(RouteSignal.model_validate({
        "schema_version": "primary-route-v1",
        "route_kind": "ordinary_tool",
        "quote": message,
        "execution_scope": "none",
        "effect_intent": "mutation_requested",
        "write_tool_name": "create_task",
        "write_arguments": {"title": "提交周报"},
    }), message, saved_plan_present=False, pending_action=None)
    decision = PrimaryHostDecision(
        "allow", "primary_v1_create_task_candidate", "ordinary_tool")
    gate = threading.Barrier(2)
    outcomes = []
    lock = threading.Lock()

    def worker(n):
        try:
            gate.wait(timeout=10)
            pid = record_primary_admission(
                session_factory=Session, conversation_id=cid, turn_id=tid,
                preturn_plan_raw=None, proposal=proposal, decision=decision,
                semantic_attempts=n)
            outcome = ("ok", pid)
        except Exception as exc:
            outcome = ("rejected", type(exc).__name__)
        with lock:
            outcomes.append(outcome)

    t1 = threading.Thread(target=worker, args=(1,), daemon=True)
    t2 = threading.Thread(target=worker, args=(2,), daemon=True)
    try:
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        assert not t1.is_alive() and not t2.is_alive()
        assert sorted(x[0] for x in outcomes) == ["ok", "rejected"], outcomes
        with Session() as db:
            rows = db.scalars(select(PrimaryRouteEvidence).where(
                PrimaryRouteEvidence.conversation_id == cid)).all()
            assert len(rows) == 1
            assert rows[0].proposal_id == next(x[1] for x in outcomes if x[0] == "ok")
            assert rows[0].preturn_snapshot_digest == snapshot_digest(None)
            assert rows[0].semantic_attempts in {1, 2}
            assert rows[0].disposition == "allow"
    finally:
        with Session.begin() as db:
            db.execute(delete(PrimaryRouteEvidence).where(
                PrimaryRouteEvidence.conversation_id == cid))
            db.execute(delete(Turn).where(Turn.conversation_id == cid))
            db.execute(delete(Message).where(Message.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()



@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires TEST_POSTGRES_URL")
def test_postgres_primary_admission_survives_worker_hard_exit():
    import subprocess
    import sys

    engine, Session = database(os.environ["TEST_POSTGRES_URL"])
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        c = Conversation(owner_id="wp-a-primary-crash-pg")
        db.add(c)
        db.flush()
        cid = c.id
        m = Message(conversation_id=cid, role="assistant",
                    content="", status="pending")
        db.add(m)
        db.flush()
        t = Turn(conversation_id=cid, request_id="wp-a-primary-crash-001",
                 assistant_message_id=m.id, state="running")
        db.add(t)
        db.flush()
        tid = t.id

    # A new interpreter commits admission, then exits without unwinding
    # Python stack frames. The parent observes its durable record independently.
    child = """
import os
from agent.primary_route_v1 import RouteSignal, bind_route_signal
from agent.primary_host_admission_v1 import PrimaryHostDecision
from services.db import database
from services.primary_route_evidence import record_primary_admission
message = '请创建任务：提交周报'
proposal = bind_route_signal(RouteSignal.model_validate({
    'schema_version': 'primary-route-v1', 'route_kind': 'ordinary_tool',
    'quote': message, 'execution_scope': 'none',
    'effect_intent': 'mutation_requested',
    'write_tool_name': 'create_task',
    'write_arguments': {'title': '提交周报'},
}), message, saved_plan_present=False, pending_action=None)
record_primary_admission(
    session_factory=database(os.environ['WP_A_DB_URL'])[1],
    conversation_id=os.environ['WP_A_CID'],
    turn_id=os.environ['WP_A_TID'], preturn_plan_raw=None,
    proposal=proposal,
    decision=PrimaryHostDecision('allow',
        'primary_v1_create_task_candidate', 'ordinary_tool'),
    semantic_attempts=1)
os._exit(23)
"""
    try:
        env = dict(os.environ)
        env.update(WP_A_DB_URL=os.environ["TEST_POSTGRES_URL"],
                   WP_A_CID=cid, WP_A_TID=tid)
        completed = subprocess.run(
            [sys.executable, "-c", child], env=env,
            capture_output=True, text=True, timeout=20)
        assert completed.returncode == 23, completed.stderr[-1500:]
        with Session() as db:
            row = db.get(PrimaryRouteEvidence, tid)
            assert row is not None
            assert row.disposition == "allow"
            assert row.semantic_attempts == 1
            assert row.preturn_snapshot_digest == snapshot_digest(None)
        with pytest.raises(RuntimeError, match="primary_evidence_already_recorded"):
            record_primary_admission(
                session_factory=Session, conversation_id=cid, turn_id=tid,
                preturn_plan_raw=None, refusal_reason="post_crash_replay")
        with Session() as db:
            assert db.get(PrimaryRouteEvidence, tid).disposition == "allow"
    finally:
        with Session.begin() as db:
            db.execute(delete(PrimaryRouteEvidence).where(
                PrimaryRouteEvidence.conversation_id == cid))
            db.execute(delete(Turn).where(Turn.conversation_id == cid))
            db.execute(delete(Message).where(Message.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
