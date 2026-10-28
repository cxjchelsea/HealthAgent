"""Atomic compare-and-swap tests for durable plan snapshots."""
import json

import pytest

from services.db import (Base, Conversation, ConversationPlan, PlanExecutionClaim, Turn, database)
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


def test_execution_claim_blocks_other_plan_writers_and_owner_can_finalize(plan_store):
    _, Session, store = plan_store
    observed = store.load_raw()
    with Session.begin() as db:
        turn = Turn(conversation_id=store.conversation_id, request_id="claim-owner-0001")
        db.add(turn)
        db.flush()
        turn_id = turn.id
        store.acquire_execution_claim_in_session(
            db, turn_id=turn_id, expected_raw=observed)

    with pytest.raises(PlanSnapshotConflict, match="plan_execution_claimed"):
        with Session.begin() as db:
            store.compare_and_swap_in_session(
                db, expected_raw=observed, snapshot={"revision": 2, "steps": []})

    with Session.begin() as db:
        store.compare_and_swap_in_session(
            db, expected_raw=observed,
            snapshot={"revision": 2, "steps": []}, owner_turn_id=turn_id)
        store.release_execution_claim_in_session(db, turn_id=turn_id)
    assert store.load() == {"revision": 2, "steps": []}
    with Session() as db:
        assert db.get(PlanExecutionClaim, store.conversation_id) is None


def test_execution_claim_rejects_stale_snapshot_before_reserving(plan_store):
    _, Session, store = plan_store
    observed = store.load_raw()
    with Session.begin() as db:
        turn = Turn(conversation_id=store.conversation_id, request_id="claim-stale-0001")
        db.add(turn)
        db.flush()
        turn_id = turn.id
    with Session.begin() as db:
        store.compare_and_swap_in_session(
            db, expected_raw=observed, snapshot={"revision": 2, "steps": []})
    with pytest.raises(PlanSnapshotConflict, match="plan_execution_guard_conflict"):
        with Session.begin() as db:
            store.acquire_execution_claim_in_session(
                db, turn_id=turn_id, expected_raw=observed)
    with Session() as db:
        assert db.get(PlanExecutionClaim, store.conversation_id) is None
