"""Gate A physical PostgreSQL: verified failure is single-winner and fenced.

All assertions run against isolated TEST_POSTGRES_URL (never production).
This proves DB concurrency and persistence; it is not a real-provider test.
"""
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import delete

from agent.dependency_plan_host import PreparedDependencyRead
from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyWorkerFence, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)
from services.dependency_failure import finalize_dependency_failure_in_session
from services.dependency_fencing import (
    acquire_dependency_fence, revoke_dependency_fence_in_session,
)
from services.dependency_recovery import inspect_dependency_orphan
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_route_evidence import snapshot_digest


def _plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "status": "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0", "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "stats", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0", "argument_bindings": {
                 "text": {"kind": "observation_ref", "source_step_id": 1,
                          "field": "definition"}}},
        ], "executions": {},
    }


@pytest.fixture
def physical():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL TEST_POSTGRES_URL")
    assert url.startswith("postgresql")
    engine, Session = database(url)
    cid, tid, did = (str(uuid.uuid4()) for _ in range(3))
    raw = json.dumps(_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="gate-a-owner"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=cid, request_id="pg-test",
                    state="running"))
        db.add(ConversationPlan(conversation_id=cid, snapshot=raw))
        db.flush()
        db.add(PlanExecutionClaim(
            conversation_id=cid, turn_id=tid, snapshot=raw))
        db.add(PrimaryRouteEvidence(
            turn_id=tid, conversation_id=cid,
            proposal_id=str(uuid.uuid4()), schema_version="primary-route-v1",
            route_kind="plan_resume", semantic_attempts=1,
            preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
    token = acquire_dependency_fence(
        Session, conversation_id=cid, turn_id=tid, expected_raw=raw)
    negative = bounded_projection(
        "mcp_reference_lookup", {"ok": False, "error": "controlled_pg_failure"})
    with Session.begin() as db:
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=tid, step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            status="observed_failure",
            arguments_json=json.dumps({"term": "RAG"}),
            receipt_json=json.dumps(negative),
            worker_generation=token.generation,
            worker_token_digest=hashlib.sha256(
                token.secret.encode("utf-8")).hexdigest()))
    binding = PreparedDependencyRead(
        owner_id="gate-a-owner", conversation_id=cid,
        turn_id=tid, request_id="pg-test", plan_revision=1,
        checkpoint_digest=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        control="resume", step_id=1, tool_name="mcp_reference_lookup",
        tool_version="1.0.0", arguments={"term": "RAG"},
        arguments_digest=arguments_digest({"term": "RAG"}))
    try:
        yield Session, cid, tid, did, raw, token, binding
    finally:
        with Session.begin() as db:
            for model in (
                    AgentStateTransition, AgentAttempt, AgentTaskState,
                    PlanToolDispatch, PrimaryRouteEvidence,
                    PlanExecutionClaim, DependencyWorkerFence,
                    ConversationPlan, Turn):
                db.execute(delete(model).where(model.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()


def _finalize(Session, raw, did, token, binding):
    with Session.begin() as db:
        return finalize_dependency_failure_in_session(
            db, binding=binding, initial_turn_raw=raw,
            expected_raw=raw, dispatch_id=did, fence=token)


def test_pg_failure_is_atomic_and_idempotency_is_fail_closed(physical):
    Session, cid, tid, did, raw, token, binding = physical
    assert _finalize(Session, raw, did, token, binding) == (
        "dependency_tool_reported_failure")
    with pytest.raises(DependencyPlanError,
                       match="dependency_failure_receipt_conflict"):
        _finalize(Session, raw, did, token, binding)
    with Session() as db:
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(PlanExecutionClaim, cid).snapshot == raw
        assert db.get(PlanToolDispatch, did).status == "failure_finalized"
        assert db.get(AgentAttempt, did).status == "failed"
        assert db.get(AgentTaskState, cid).state == "blocked"
        assert db.query(AgentStateTransition).filter_by(
            conversation_id=cid, reason_code="verified_step_failed").count() == 1
    inspection = inspect_dependency_orphan(
        Session, owner_id="gate-a-owner", conversation_id=cid, turn_id=tid)
    assert inspection.classification == "verified_failure_no_replay"


def test_pg_concurrent_finalizers_commit_exactly_once(physical):
    Session, cid, _tid, did, raw, token, binding = physical

    def run():
        try:
            return _finalize(Session, raw, did, token, binding)
        except DependencyPlanError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(results) == [
        "dependency_failure_receipt_conflict",
        "dependency_tool_reported_failure"]
    with Session() as db:
        assert db.query(AgentAttempt).filter_by(
            conversation_id=cid).count() == 1
        assert db.query(AgentStateTransition).filter_by(
            conversation_id=cid, reason_code="verified_step_failed").count() == 1
        assert db.get(AgentTaskState, cid).revision == 1
        assert db.get(ConversationPlan, cid).snapshot == raw


def test_pg_late_finalizer_after_worker_fenced_is_rejected(physical):
    Session, cid, tid, did, raw, token, binding = physical
    with Session.begin() as db:
        revoke_dependency_fence_in_session(
            db, conversation_id=cid, turn_id=tid)
    with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
        _finalize(Session, raw, did, token, binding)
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "observed_failure"
        assert db.get(AgentAttempt, did) is None
        assert db.get(AgentTaskState, cid) is None
        assert db.get(ConversationPlan, cid).snapshot == raw


def test_pg_stale_snapshot_conflict_does_not_finalize(physical):
    Session, cid, _tid, did, raw, token, binding = physical
    changed = json.loads(raw)
    changed["steps"][0]["goal"] = "changed-after-admission"
    with Session.begin() as db:
        db.get(ConversationPlan, cid).snapshot = json.dumps(
            changed, ensure_ascii=False)
    with pytest.raises(DependencyPlanError, match="dependency_failure_claim_conflict"):
        _finalize(Session, raw, did, token, binding)
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "observed_failure"
        assert db.get(AgentAttempt, did) is None
        assert db.get(AgentTaskState, cid) is None
