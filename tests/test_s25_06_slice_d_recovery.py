"""S25-06 orphan recovery: explicit operator gates and immutable evidence fencing."""
import json

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, Message, PlanExecutionClaim,
    PlanRecoveryAudit, PlanToolDispatch, PrimaryRouteEvidence, Turn, database,
)
from services.dependency_recovery import (
    inspect_dependency_orphan, reconcile_dependency_orphan,
)
from services.evidence_store import arguments_digest, bounded_projection
from services.migrate import migrate
from services.primary_route_evidence import snapshot_digest


def _plan():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1, "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "lookup", "status": "pending",
             "depends_on": [], "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG",
                          "source_quote": "RAG"}}},
            {"id": 2, "goal": "count", "status": "pending",
             "depends_on": [1], "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "text": {"kind": "observation_ref", "source_step_id": 1,
                          "field": "definition"}}},
        ], "executions": {},
    }


@pytest.fixture
def orphan(tmp_path):
    url = "sqlite:///" + (tmp_path / "orphan.db").as_posix()
    migrate(url)
    engine, Session = database(url)
    raw = json.dumps(_plan(), ensure_ascii=False)
    with Session.begin() as db:
        db.add(Conversation(id="cid", owner_id="actual-owner"))
        db.add(Message(id="mid", conversation_id="cid", role="assistant",
                       content="", status="pending"))
        db.flush()
        db.add(Turn(id="tid", conversation_id="cid", request_id="req",
                    assistant_message_id="mid", state="running"))
        db.add(ConversationPlan(conversation_id="cid", snapshot=raw))
        db.flush()
        db.add(PlanExecutionClaim(
            conversation_id="cid", turn_id="tid", snapshot=raw))
        db.add(PrimaryRouteEvidence(
            turn_id="tid", conversation_id="cid", proposal_id="test-proposal",
            schema_version="primary-route-v1", route_kind="plan_resume",
            semantic_attempts=1, preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="primary_v1_plan_execution_candidate"))
        db.add(PlanToolDispatch(
            id="d1", conversation_id="cid", turn_id="tid", step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            arguments_json='{"term":"RAG"}', status="dispatch_unknown"))
    yield Session
    engine.dispose()


def _reconcile(Session, inspection, **overrides):
    kwargs = {
        "owner_id": "actual-owner", "conversation_id": "cid",
        "turn_id": "tid", "operator_id": "operator-X",
        "expected_plan_sha256": inspection.plan_sha256,
        "expected_evidence_sha256": inspection.evidence_sha256,
        "worker_terminated_confirmed": True,
        "operator_authorized": True,
    }
    kwargs.update(overrides)
    return reconcile_dependency_orphan(Session, **kwargs)


def _refused(Session, inspection, expected, **overrides):
    with pytest.raises(DependencyPlanError) as err:
        _reconcile(Session, inspection, **overrides)
    assert err.value.code == expected


def test_unknown_dispatch_is_preserved_and_explicitly_fenced(orphan):
    inspection = inspect_dependency_orphan(
        orphan, owner_id="actual-owner", conversation_id="cid", turn_id="tid")
    assert inspection.classification == "uncertain_no_replay"
    assert inspection.dispatch_states == ((1, "dispatch_unknown"),)
    assert _reconcile(orphan, inspection) == inspection
    with orphan() as db:
        assert db.get(PlanExecutionClaim, "cid") is None
        turn = db.get(Turn, "tid")
        assert (turn.state, turn.failure_code) == (
            "partial", "dependency_reconciliation_required")
        message = db.get(Message, "mid")
        assert message.status == "partial"
        assert "不会自动重试" in message.content
        assert db.get(PlanToolDispatch, "d1").status == "dispatch_unknown"
        assert db.get(ConversationPlan, "cid").snapshot == json.dumps(
            _plan(), ensure_ascii=False)
        audits = db.query(PlanRecoveryAudit).all()
        assert len(audits) == 1 and audits[0].operator_id == "operator-X"
    _refused(orphan, inspection, "dependency_recovery_claim_unverified")


@pytest.mark.parametrize("overrides", [
    {"operator_authorized": False},
    {"worker_terminated_confirmed": False},
    {"operator_id": ""},
    {"expected_plan_sha256": "f" * 64},
    {"expected_evidence_sha256": "e" * 64},
])
def test_operator_and_checkpoint_attestation_fail_closed(orphan, overrides):
    inspection = inspect_dependency_orphan(
        orphan, owner_id="actual-owner", conversation_id="cid", turn_id="tid")
    code = ("dependency_recovery_evidence_changed"
            if "expected_plan_sha256" in overrides
            or "expected_evidence_sha256" in overrides
            else "dependency_recovery_operator_required")
    _refused(orphan, inspection, code, **overrides)
    with orphan() as db:
        assert db.get(Turn, "tid").state == "running"
        assert db.get(PlanExecutionClaim, "cid") is not None
        assert db.query(PlanRecoveryAudit).count() == 0


def test_owner_and_dispatch_changes_after_inspection_are_not_accepted(orphan):
    with pytest.raises(DependencyPlanError, match="dependency_recovery_claim_unverified"):
        inspect_dependency_orphan(
            orphan, owner_id="wrong-owner", conversation_id="cid", turn_id="tid")
    inspection = inspect_dependency_orphan(
        orphan, owner_id="actual-owner", conversation_id="cid", turn_id="tid")
    with orphan.begin() as db:
        db.get(PlanToolDispatch, "d1").status = "observed"
        db.get(PlanToolDispatch, "d1").receipt_json = '{"ok":true,"definition":"RAG"}'
    _refused(orphan, inspection, "dependency_recovery_evidence_changed")
    with orphan() as db:
        assert db.get(Turn, "tid").state == "running"


def test_wrong_turn_plan_version_or_primary_admission_cannot_be_recovered(orphan):
    with orphan.begin() as db:
        db.get(PrimaryRouteEvidence, "tid").disposition = "deny"
    with pytest.raises(DependencyPlanError, match="dependency_recovery_claim_unverified"):
        inspect_dependency_orphan(
            orphan, owner_id="actual-owner", conversation_id="cid", turn_id="tid")


@pytest.mark.parametrize("status,receipt", [
    ("dispatch_unknown", '{"ok":true}'),
    ("observed", None),
    ("observed_failure", '{"ok":true}'),
])
def test_recovery_rejects_inconsistent_receipt_state(orphan, status, receipt):
    with orphan.begin() as db:
        dispatch = db.get(PlanToolDispatch, "d1")
        dispatch.status = status
        dispatch.receipt_json = receipt
    with pytest.raises(DependencyPlanError, match="dependency_recovery_untrusted_dispatch"):
        inspect_dependency_orphan(
            orphan, owner_id="actual-owner", conversation_id="cid", turn_id="tid")
    with orphan() as db:
        assert db.get(PlanExecutionClaim, "cid") is not None


def test_recovery_revalidates_completed_attempt_metadata_after_inspection(orphan):
    receipt = bounded_projection("mcp_reference_lookup", {
        "ok": True, "definition": "retrieval augmented generation",
        "source": "trusted-reference"})
    with orphan.begin() as db:
        row = db.get(ConversationPlan, "cid")
        plan = json.loads(row.snapshot)
        plan["steps"][0]["status"] = "completed"
        plan["executions"]["1"] = {
            "dispatch_id": "d1", "step_id": 1, "plan_revision": 1,
            "tool_name": "mcp_reference_lookup", "tool_version": "1.0.0",
            "resolved_arguments_digest": arguments_digest({"term": "RAG"}),
            "source_evidence_refs": [],
        }
        raw = json.dumps(plan, ensure_ascii=False)
        row.snapshot = raw
        db.get(PlanExecutionClaim, "cid").snapshot = raw
        dispatch = db.get(PlanToolDispatch, "d1")
        dispatch.status = "checkpointed"
        dispatch.receipt_json = json.dumps(receipt)
        db.add(AgentAttempt(
            id="d1", conversation_id="cid", turn_id="tid",
            run_id="tid", step_id=1,
            tool_name="mcp_reference_lookup", tool_version="1.0.0",
            arguments_digest=arguments_digest({"term": "RAG"}),
            status="succeeded", result_projection=json.dumps(receipt)))
    checked = inspect_dependency_orphan(
        orphan, owner_id="actual-owner", conversation_id="cid", turn_id="tid")
    assert checked.classification == "checkpointed_unfinalized"
    with orphan.begin() as db:
        db.get(AgentAttempt, "d1").error_code = "mutated_after_review"
    _refused(orphan, checked, "dependency_recovery_evidence_changed")
    with orphan() as db:
        assert db.get(PlanExecutionClaim, "cid") is not None
        assert db.get(Turn, "tid").state == "running"
        assert db.query(PlanRecoveryAudit).count() == 0
