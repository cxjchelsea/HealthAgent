"""S25-06 Step 5: physical PostgreSQL orphan recovery and generation fencing.

This exercises real PostgreSQL transactions, not a SQLite substitute. External
operator authority/worker-death verifiers remain synthetic and are NOT a
production IAM integration or a physical kill/restart test.
"""
import json
import os
import uuid

import pytest
from sqlalchemy import delete

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    Conversation, ConversationPlan, DependencyWorkerFence, PlanExecutionClaim,
    PlanRecoveryAudit, PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)
from services.dependency_fencing import (
    acquire_dependency_fence, assert_dependency_fence_in_session,
)
from services.dependency_recovery import (
    inspect_dependency_orphan, reconcile_dependency_orphan,
)
from services.primary_route_evidence import snapshot_digest


def _saved_plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {
                "id": 1, "goal": "lookup", "status": "pending",
                "depends_on": [], "expected_tool": "mcp_reference_lookup",
                "tool_version": "1.0.0",
                "argument_bindings": {
                    "term": {"kind": "literal", "value": "RAG",
                             "source_quote": "RAG"},
                },
            },
            {
                "id": 2, "goal": "count", "status": "pending",
                "depends_on": [1], "expected_tool": "text_stats",
                "tool_version": "1.0.0",
                "argument_bindings": {
                    "text": {"kind": "observation_ref",
                             "source_step_id": 1, "field": "definition"},
                },
            },
        ],
        "executions": {},
    }


@pytest.fixture
def physical_orphan():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("requires isolated real PostgreSQL")
    assert url.startswith("postgresql")
    engine, Session = database(url)
    cid, tid, did = (str(uuid.uuid4()) for _ in range(3))
    raw = json.dumps(_saved_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id=cid, owner_id="physical-owner"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=cid,
                    request_id="step5-orphan", state="running"))
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
    fence = acquire_dependency_fence(
        Session, conversation_id=cid, turn_id=tid, expected_raw=raw)
    # Committed before-call dispatch_unknown: the external tool may have run,
    # but no trusted Receipt exists. Never silently infer its outcome.
    with Session.begin() as db:
        db.add(PlanToolDispatch(
            id=did, conversation_id=cid, turn_id=tid, step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            worker_generation=fence.generation,
            worker_token_digest=db.get(DependencyWorkerFence, cid).token_digest,
            arguments_json=json.dumps({"term": "RAG"}),
            status="dispatch_unknown"))
    try:
        yield Session, cid, tid, did, raw, fence
    finally:
        with Session.begin() as db:
            for model in (
                PlanRecoveryAudit, PlanToolDispatch, PrimaryRouteEvidence,
                PlanExecutionClaim, DependencyWorkerFence, ConversationPlan,
                Turn,
            ):
                db.execute(delete(model).where(model.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()


def _reconcile(Session, *, cid, tid, inspection, verifier=lambda *_: True):
    return reconcile_dependency_orphan(
        Session, owner_id="physical-owner", conversation_id=cid,
        turn_id=tid, operator_id="synthetic-operator",
        expected_plan_sha256=inspection.plan_sha256,
        expected_evidence_sha256=inspection.evidence_sha256,
        operator_authorized=True, worker_terminated_confirmed=True,
        operator_access_verifier=lambda *_: True,
        worker_termination_verifier=verifier,
    )


def test_postgres_unknown_dispatch_requires_external_proof_then_fences_old_worker(
        physical_orphan):
    Session, cid, tid, did, raw, fence = physical_orphan
    evidence = inspect_dependency_orphan(
        Session, owner_id="physical-owner", conversation_id=cid, turn_id=tid)
    assert evidence.classification == "uncertain_no_replay"
    assert evidence.dispatch_states == ((1, "dispatch_unknown"),)

    with pytest.raises(DependencyPlanError, match="dependency_recovery_worker_not_confirmed"):
        _reconcile(Session, cid=cid, tid=tid, inspection=evidence,
                   verifier=lambda *_: False)
    with Session() as db:
        assert db.get(Turn, tid).state == "running"
        assert db.get(PlanExecutionClaim, cid) is not None
        assert db.get(DependencyWorkerFence, cid).state == "active"

    released = _reconcile(
        Session, cid=cid, tid=tid, inspection=evidence)
    assert released.classification == "uncertain_no_replay"
    with Session.begin() as db:
        assert db.get(Turn, tid).state == "partial"
        assert db.get(Turn, tid).failure_code == "dependency_reconciliation_required"
        assert db.get(PlanExecutionClaim, cid) is None
        assert db.get(DependencyWorkerFence, cid).state == "fenced"
        assert db.get(ConversationPlan, cid).snapshot == raw
        dispatch = db.get(PlanToolDispatch, did)
        assert dispatch.status == "dispatch_unknown"
        assert dispatch.receipt_json is None
        assert db.query(PlanRecoveryAudit).filter_by(
            conversation_id=cid).count() == 1
        with pytest.raises(DependencyPlanError, match="dependency_worker_fenced"):
            assert_dependency_fence_in_session(
                db, conversation_id=cid, turn_id=tid, fence=fence)

    with pytest.raises(DependencyPlanError, match="dependency_recovery_claim_unverified"):
        inspect_dependency_orphan(
            Session, owner_id="physical-owner", conversation_id=cid, turn_id=tid)
