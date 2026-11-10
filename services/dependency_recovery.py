"""S25-06 durable orphan inspection and explicit, non-replaying release.

Operator-only service boundary: not registered as HTTP, tool, MCP, or Agent
capability. Caller MUST authenticate the operator independently and establish
that the original worker/Turn is dead. Merely holding a conversation ID is
never proof the worker stopped. No Dispatch is deleted or reclassified here.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, Message, PlanExecutionClaim,
    PlanRecoveryAudit, PlanToolDispatch, PrimaryRouteEvidence, Turn,
)


@dataclass(frozen=True)
class DependencyRecoveryInspection:
    owner_id: str
    conversation_id: str
    turn_id: str
    plan_sha256: str
    evidence_sha256: str
    classification: str
    # No raw tool data, arguments or model text exposed.
    dispatch_states: tuple[tuple[int, str], ...]


def _deny(code: str):
    raise DependencyPlanError(code)


def _inspect_locked(db, *, owner_id, conversation_id, turn_id):
    owner = db.get(Conversation, conversation_id)
    plan = db.execute(select(ConversationPlan).where(
        ConversationPlan.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    claim = db.execute(select(PlanExecutionClaim).where(
        PlanExecutionClaim.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    turn = db.get(Turn, turn_id)
    primary = db.get(PrimaryRouteEvidence, turn_id)
    if (owner is None or owner.owner_id != owner_id or plan is None
            or claim is None or claim.turn_id != turn_id
            or claim.snapshot != plan.snapshot
            or turn is None or turn.conversation_id != conversation_id
            or turn.state != "running"
            or primary is None or primary.conversation_id != conversation_id
            or primary.disposition != "allow"
            or primary.admission_reason != "primary_v1_plan_execution_candidate"
            or primary.route_kind not in {"plan_execute_first", "plan_resume"}):
        _deny("dependency_recovery_claim_unverified")
    try:
        data = json.loads(plan.snapshot)
    except (ValueError, TypeError):
        _deny("dependency_recovery_invalid_plan")
    if (type(data) is not dict or data.get("plan_schema_version") != "dependency-plan-v1"
            or data.get("plan_executor_version") != "dependency-plan-read-v1"):
        _deny("dependency_recovery_invalid_plan")
    dispatches = db.scalars(select(PlanToolDispatch).where(
        PlanToolDispatch.conversation_id == conversation_id,
        PlanToolDispatch.turn_id == turn_id
    ).order_by(PlanToolDispatch.step_id, PlanToolDispatch.id)).all()
    allowed = {"dispatch_unknown", "observed", "observed_failure", "checkpointed"}
    if any(item.status not in allowed for item in dispatches):
        _deny("dependency_recovery_unrecognized_dispatch")
    # A single Turn may have a committed prefix followed by one uncertain step.
    states = tuple((item.step_id, item.status) for item in dispatches)
    if len({item.step_id for item in dispatches}) != len(dispatches):
        _deny("dependency_recovery_duplicate_dispatch")
    try:
        steps = data["steps"]
        executions = data["executions"]
        if (type(steps) is not list or type(executions) is not dict
                or not 2 <= len(steps) <= 8):
            _deny("dependency_recovery_invalid_plan")
        by_id = {x["id"]: x for x in steps}
        if len(by_id) != len(steps):
            _deny("dependency_recovery_invalid_plan")
    except (KeyError, TypeError, ValueError):
        _deny("dependency_recovery_invalid_plan")
    for item in dispatches:
        step = by_id.get(item.step_id)
        if (step is None or item.tool_name != step.get("expected_tool")
                or item.tool_version != step.get("tool_version")):
            _deny("dependency_recovery_untrusted_dispatch")
        record = executions.get(str(item.step_id))
        attempt = db.get(AgentAttempt, item.id)
        if item.status == "checkpointed":
            if (step.get("status") != "completed" or type(record) is not dict
                    or record.get("dispatch_id") != item.id
                    or attempt is None or attempt.status != "succeeded"):
                _deny("dependency_recovery_untrusted_dispatch")
        elif (step.get("status") != "pending" or record is not None
              or attempt is not None):
            _deny("dependency_recovery_untrusted_dispatch")
    # Hash stable IDs and states only, not mutable/raw tool receipts.
    evidence = [{"id": item.id, "step": item.step_id, "status": item.status}
                for item in dispatches]
    evidence_sha = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    uncertain = any(s in {"dispatch_unknown", "observed", "observed_failure"}
                    for _, s in states)
    classification = ("uncertain_no_replay" if uncertain
                      else "checkpointed_unfinalized" if states
                      else "no_dispatch")
    inspection = DependencyRecoveryInspection(
        owner_id=owner_id, conversation_id=conversation_id, turn_id=turn_id,
        plan_sha256=hashlib.sha256(plan.snapshot.encode()).hexdigest(),
        evidence_sha256=evidence_sha,
        classification=classification, dispatch_states=states,
    )
    return inspection, claim, turn


def inspect_dependency_orphan(session_factory, *, owner_id, conversation_id, turn_id):
    """Read-only, does not establish that the worker is dead."""
    with session_factory.begin() as db:
        inspection, _, _ = _inspect_locked(
            db, owner_id=owner_id, conversation_id=conversation_id, turn_id=turn_id)
        return inspection


def reconcile_dependency_orphan(
    session_factory, *, owner_id: str, conversation_id: str, turn_id: str,
    operator_id: str, expected_plan_sha256: str,
    expected_evidence_sha256: str, worker_terminated_confirmed: bool,
    operator_authorized: bool = False,
):
    """Explicit operator attestation. Operator authentication is EXTERNAL.

    Only after independently confirming worker termination may callers provide
    operator_authorized=True. This does NOT authenticate them; do not expose
    this function on any public route or model-callable tool.
    """
    if (operator_authorized is not True
            or worker_terminated_confirmed is not True
            or type(operator_id) is not str or not operator_id.strip()
            or not all(type(x) is str and len(x) == 64
                       and all(c in "0123456789abcdef" for c in x)
                       for x in (expected_plan_sha256, expected_evidence_sha256))):
        _deny("dependency_recovery_operator_required")
    with session_factory.begin() as db:
        inspection, claim, turn = _inspect_locked(
            db, owner_id=owner_id, conversation_id=conversation_id, turn_id=turn_id)
        if (inspection.plan_sha256 != expected_plan_sha256
                or inspection.evidence_sha256 != expected_evidence_sha256):
            _deny("dependency_recovery_evidence_changed")
        # Never claim unknown effects succeeded, and never erase the fence.
        # Recovery only terminates the abandoned Turn + releases its Claim.
        turn.state = "partial"
        turn.failure_code = ("dependency_reconciliation_required"
                             if inspection.classification == "uncertain_no_replay"
                             else "dependency_turn_recovered")
        if turn.assistant_message_id is not None:
            message = db.get(Message, turn.assistant_message_id)
            if message is None:
                _deny("dependency_recovery_message_missing")
            message.status = "partial"
            message.content = ("执行状态待人工核对，系统不会自动重试。"
                               if inspection.classification == "uncertain_no_replay"
                               else "已保留已验证步骤；原执行进程中断。")
        db.add(PlanRecoveryAudit(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            turn_id=turn_id, operator_id=operator_id.strip(),
            decision="dependency_orphan_released_no_replay",
            evidence_sha256=inspection.evidence_sha256,
            snapshot_sha256=inspection.plan_sha256))
        db.delete(claim)
        db.flush()
        return inspection
