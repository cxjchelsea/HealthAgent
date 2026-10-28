"""Optional PostgreSQL integration test for row-lock coordinated plan claims.

Run with TEST_POSTGRES_URL=postgresql+psycopg://... pytest -q
tests/test_s25_02_m3_postgres_claim_concurrency.py
"""
import json
import os
import threading

import pytest
from sqlalchemy import delete

from services.db import (Base, Conversation, ConversationPlan, PlanExecutionClaim, Turn, database)
from services.plan_store import ConversationPlanStore, PlanSnapshotConflict


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires TEST_POSTGRES_URL PostgreSQL integration database")
def test_competing_plan_writer_waits_for_claim_then_fails_closed():
    url = os.environ["TEST_POSTGRES_URL"]
    engine, Session = database(url)
    Base.metadata.create_all(engine)
    claimed = threading.Event()
    let_owner_commit = threading.Event()
    outcomes = []
    with Session.begin() as db:
        conversation = Conversation(owner_id="m3-postgres-claim-test")
        db.add(conversation)
        db.flush()
        cid = conversation.id
        snapshot = json.dumps({"revision": 1, "steps": []})
        db.add(ConversationPlan(conversation_id=cid, snapshot=snapshot))
        owner = Turn(conversation_id=cid, request_id="claim-owner")
        writer = Turn(conversation_id=cid, request_id="claim-writer")
        db.add_all([owner, writer])
        db.flush()
        owner_id = owner.id
    store = ConversationPlanStore(Session, cid)

    def reserve_claim():
        try:
            with Session.begin() as db:
                store.acquire_execution_claim_in_session(
                    db, turn_id=owner_id, expected_raw=snapshot)
                claimed.set()
                if not let_owner_commit.wait(timeout=10):
                    raise TimeoutError("test did not release claim owner")
            outcomes.append(("owner", "committed"))
        except Exception as exc:  # surfaced below as an assertion failure
            outcomes.append(("owner_error", type(exc).__name__ + ":" + str(exc)))

    def competing_write():
        try:
            if not claimed.wait(timeout=10):
                raise TimeoutError("claim owner did not acquire row lock")
            with Session.begin() as db:
                store.compare_and_swap_in_session(
                    db, expected_raw=snapshot,
                    snapshot={"revision": 2, "steps": []})
            outcomes.append(("writer", "unexpected_success"))
        except PlanSnapshotConflict as exc:
            outcomes.append(("writer", str(exc)))
        except Exception as exc:
            outcomes.append(("writer_error", type(exc).__name__ + ":" + str(exc)))

    owner_thread = threading.Thread(target=reserve_claim, daemon=True)
    writer_thread = threading.Thread(target=competing_write, daemon=True)
    owner_thread.start()
    writer_thread.start()
    try:
        assert claimed.wait(timeout=10), "claim owner did not acquire claim"
        # Let the competing transaction reach the row-lock wait before release.
        threading.Event().wait(0.2)
    finally:
        let_owner_commit.set()
    owner_thread.join(timeout=10)
    writer_thread.join(timeout=10)
    try:
        assert not owner_thread.is_alive() and not writer_thread.is_alive()
        assert ("owner", "committed") in outcomes, outcomes
        assert ("writer", "plan_execution_claimed") in outcomes, outcomes
        assert store.load() == {"revision": 1, "steps": []}
    finally:
        with Session.begin() as db:
            db.execute(delete(ConversationPlan).where(
                ConversationPlan.conversation_id == cid))
            db.execute(delete(PlanExecutionClaim).where(
                PlanExecutionClaim.conversation_id == cid))
            db.execute(__import__("sqlalchemy").delete(Turn).where(Turn.conversation_id == cid))
            db.delete(db.get(Conversation, cid))
        engine.dispose()
