"""S25-06 Worker-generation fencing, rollback and legacy M4 isolation."""
import hashlib
import json

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    Conversation, ConversationPlan, DependencyWorkerFence,
    PlanExecutionClaim, PlanToolDispatch, Turn, database,
)
from services.dependency_fencing import (
    acquire_dependency_fence, assert_dependency_fence_in_session,
    revoke_dependency_fence_in_session,
)
from services.migrate import migrate
from services.plan_dispatch import PlanDispatchJournal


@pytest.fixture
def prepared(tmp_path):
    url = "sqlite:///" + (tmp_path / "fence.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    raw = json.dumps({"plan_schema_version": "dependency-plan-v1"})
    with Session.begin() as db:
        db.add(Conversation(id="cid", owner_id="owner"))
        db.flush()
        db.add(Turn(id="t1", conversation_id="cid",
                    request_id="r1", state="running"))
        db.add(ConversationPlan(conversation_id="cid", snapshot=raw))
        db.flush()
        db.add(PlanExecutionClaim(
            conversation_id="cid", turn_id="t1", snapshot=raw))
    yield Session, raw
    engine.dispose()


def _token(Session, raw, turn="t1"):
    return acquire_dependency_fence(
        Session, conversation_id="cid", turn_id=turn, expected_raw=raw)


def test_generation_revoke_and_next_turn_fail_closed(prepared):
    Session, raw = prepared
    first = _token(Session, raw)
    assert first.generation == 1
    with Session.begin() as db:
        assert_dependency_fence_in_session(
            db, conversation_id="cid", turn_id="t1", fence=first)
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        _token(Session, raw)  # same Turn cannot mint a replacement secret
    with Session.begin() as db:
        revoke_dependency_fence_in_session(
            db, conversation_id="cid", turn_id="t1")
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        with Session.begin() as db:
            assert_dependency_fence_in_session(
                db, conversation_id="cid", turn_id="t1", fence=first)
    with Session.begin() as db:
        db.get(Turn, "t1").state = "partial"
        db.delete(db.get(PlanExecutionClaim, "cid"))
        db.flush()
        db.add(Turn(id="t2", conversation_id="cid",
                    request_id="r2", state="running"))
        db.flush()
        db.add(PlanExecutionClaim(
            conversation_id="cid", turn_id="t2", snapshot=raw))
    second = _token(Session, raw, "t2")
    assert second.generation == 2
    assert second.secret != first.secret
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        with Session.begin() as db:
            assert_dependency_fence_in_session(
                db, conversation_id="cid", turn_id="t1", fence=first)
    with Session() as db:
        row = db.get(DependencyWorkerFence, "cid")
        assert row.turn_id == "t2" and row.state == "active"
        assert row.token_digest == hashlib.sha256(
            second.secret.encode("utf-8")).hexdigest()


def test_receipt_requires_original_generation_after_revoke(prepared):
    Session, raw = prepared
    token = _token(Session, raw)
    with Session.begin() as db:
        db.add(PlanToolDispatch(
            id="d1", conversation_id="cid", turn_id="t1", step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            worker_generation=token.generation,
            worker_token_digest=hashlib.sha256(
                token.secret.encode("utf-8")).hexdigest(),
            arguments_json='{"term":"RAG"}', status="dispatch_unknown"))
    journal = PlanDispatchJournal(Session, "cid", "t1")
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        journal.record_receipt(
            "d1", {"ok": True, "definition": "RAG"})
    with Session.begin() as db:
        revoke_dependency_fence_in_session(
            db, conversation_id="cid", turn_id="t1")
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        journal.record_receipt(
            "d1", {"ok": True, "definition": "RAG"}, fence=token)
    with Session() as db:
        entry = db.get(PlanToolDispatch, "d1")
        assert entry.status == "dispatch_unknown"
        assert entry.receipt_json is None


def test_legacy_dispatch_receipt_isolated_from_new_fence(prepared):
    Session, raw = prepared
    _token(Session, raw)
    with Session.begin() as db:
        db.add(PlanToolDispatch(
            id="legacy", conversation_id="cid", turn_id="t1", step_id=99,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            arguments_json='{"term":"RAG"}', status="dispatch_unknown"))
    journal = PlanDispatchJournal(Session, "cid", "t1")
    journal.record_receipt(
        "legacy", {"ok": True, "definition": "RAG"})
    with Session() as db:
        assert db.get(PlanToolDispatch, "legacy").status == "observed"


def test_cannot_revoke_other_turn_or_inactive_generation(prepared):
    Session, raw = prepared
    _token(Session, raw)
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        with Session.begin() as db:
            revoke_dependency_fence_in_session(
                db, conversation_id="cid", turn_id="t2")
    with Session.begin() as db:
        revoke_dependency_fence_in_session(
            db, conversation_id="cid", turn_id="t1")
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        with Session.begin() as db:
            revoke_dependency_fence_in_session(
                db, conversation_id="cid", turn_id="t1")
