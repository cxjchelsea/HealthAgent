"""Durable intent/receipt/commit tri-state and no-repeat invariants."""
import json
import pytest
from sqlalchemy import select
from services.db import (Base, Conversation, Turn, PlanToolDispatch, database)
from services.plan_dispatch import PlanDispatchJournal, DispatchConflict


def _setup(tmp_path):
    engine, Session = database(f"sqlite:///{tmp_path / 'dispatch.db'}")
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        c = Conversation(owner_id="test")
        db.add(c)
        db.flush()
        cid = c.id
        t = Turn(conversation_id=cid, request_id="r0000001", state="running")
        db.add(t)
        db.flush()
        tid = t.id
    return engine, Session, cid, tid


def test_intent_before_dispatch_and_checkpoint_atomic(tmp_path):
    engine, Session, cid, tid = _setup(tmp_path)
    try:
        journal = PlanDispatchJournal(Session, cid, tid)
        dispatch_id = journal.begin(1, "calculate", {"expression": "4*7"})
        with Session() as db:
            row = db.get(PlanToolDispatch, dispatch_id)
            assert row.status == "dispatch_unknown"
            assert row.receipt_json is None
        with pytest.raises(DispatchConflict, match="unreconciled_dispatch"):
            journal.begin(1, "calculate", {"expression": "4*7"})
        journal.record_receipt(dispatch_id, {"ok": True, "value": 28})
        with Session() as db:
            row = db.get(PlanToolDispatch, dispatch_id)
            assert row.status == "observed"
            assert json.loads(row.receipt_json)["value"] == "28"
        with pytest.raises(DispatchConflict, match="unreconciled_dispatch"):
            journal.begin(1, "calculate", {"expression": "4*7"})
        with Session.begin() as db:
            journal.checkpoint_in_session(db)
            db.get(Turn, tid).state = "completed"
        with Session() as db:
            assert db.get(PlanToolDispatch, dispatch_id).status == "checkpointed"
        with pytest.raises(DispatchConflict, match="turn_not_running"):
            journal.begin(2, "calculate", {"expression": "9+3"})
    finally:
        engine.dispose()


def test_crashed_dispatch_remains_unknown_after_new_journal_object(tmp_path):
    engine, Session, cid, tid = _setup(tmp_path)
    try:
        first = PlanDispatchJournal(Session, cid, tid)
        first.begin(1, "calculate", {"expression": "4*7"})
        recovered = PlanDispatchJournal(Session, cid, tid)
        with pytest.raises(DispatchConflict, match="unreconciled_dispatch"):
            recovered.begin(1, "calculate", {"expression": "4*7"})
        with Session() as db:
            assert db.scalar(select(PlanToolDispatch.status)) == "dispatch_unknown"
    finally:
        engine.dispose()
