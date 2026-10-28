"""Atomic compare-and-swap tests for durable plan snapshots."""
import json

import pytest

from services.db import Base, Conversation, ConversationPlan, database
from services.plan_store import ConversationPlanStore, PlanSnapshotConflict


@pytest.fixture
def plan_store():
    engine, Session = database("sqlite://")
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        conversation = Conversation(owner_id="m3-cas-test")
        db.add(conversation)
        db.flush()
        cid = conversation.id
        db.add(ConversationPlan(
            conversation_id=cid,
            snapshot=json.dumps({"revision": 1, "steps": []})))
    yield engine, Session, ConversationPlanStore(Session, cid)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_compare_and_swap_updates_only_the_exact_observed_snapshot(plan_store):
    _, Session, store = plan_store
    observed = store.load_raw()
    with Session.begin() as db:
        store.compare_and_swap_in_session(
            db, expected_raw=observed,
            snapshot={"revision": 2, "steps": [{"id": 1}]})
    assert store.load() == {"revision": 2, "steps": [{"id": 1}]}


def test_stale_compare_and_swap_cannot_overwrite_newer_snapshot(plan_store):
    _, Session, store = plan_store
    stale = store.load_raw()
    with Session.begin() as db:
        store.compare_and_swap_in_session(
            db, expected_raw=stale, snapshot={"revision": 2, "steps": []})

    with pytest.raises(PlanSnapshotConflict, match="plan_snapshot_conflict"):
        with Session.begin() as db:
            store.compare_and_swap_in_session(
                db, expected_raw=stale,
                snapshot={"revision": 99, "steps": [{"id": 99}]})

    assert store.load() == {"revision": 2, "steps": []}


def test_compare_and_swap_refuses_create_if_row_appeared(plan_store):
    _, Session, store = plan_store
    with pytest.raises(PlanSnapshotConflict, match="plan_snapshot_conflict"):
        with Session.begin() as db:
            store.compare_and_swap_in_session(
                db, expected_raw=None,
                snapshot={"revision": 2, "steps": []})
    assert store.load() == {"revision": 1, "steps": []}


def test_compare_and_swap_creates_only_when_snapshot_was_absent():
    engine, Session = database("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session.begin() as db:
            conversation = Conversation(owner_id="m3-cas-absent")
            db.add(conversation)
            db.flush()
            cid = conversation.id
        store = ConversationPlanStore(Session, cid)
        with Session.begin() as db:
            store.compare_and_swap_in_session(
                db, expected_raw=None,
                snapshot={"revision": 1, "steps": []})
        assert store.load() == {"revision": 1, "steps": []}
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
