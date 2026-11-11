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
from typing import Callable

from sqlalchemy import select

from agent.dependency_plan_schema import DependencyPlanError
from services.evidence_store import arguments_digest, bounded_projection
from services.dependency_fencing import revoke_dependency_fence_in_session
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
    # Receipt writers lock dispatch rows. Lock the same rows for the entire
    # inspection/reconciliation transaction, or an in-flight receipt could
    # change after the operator's evidence digest is checked.
    dispatches = db.scalars(select(PlanToolDispatch).where(
        PlanToolDispatch.conversation_id == conversation_id,
        PlanToolDispatch.turn_id == turn_id
    ).order_by(PlanToolDispatch.step_id, PlanToolDispatch.id).with_for_update()).all()
    allowed = {"dispatch_unknown", "observed", "observed_failure",
               "checkpointed", "failure_finalized"}
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
    locked_attempts = {}
    for item in dispatches:
        # Lock the Attempt alongside its Dispatch: a concurrent metadata
        # update must not invalidate the operator-approved evidence digest.
        locked_attempts[item.id] = db.execute(select(AgentAttempt).where(
            AgentAttempt.id == item.id
        ).with_for_update()).scalar_one_or_none()
        # An observed receipt is bounded evidence, not a checkpoint; malformed
        # or inconsistent receipt states still require manual investigation.
        if item.status == "dispatch_unknown":
            if item.receipt_json is not None:
                _deny("dependency_recovery_untrusted_dispatch")
        elif item.status in {"observed", "observed_failure"}:
            try:
                partial_receipt = json.loads(item.receipt_json)
            except (TypeError, ValueError):
                _deny("dependency_recovery_untrusted_dispatch")
            if (type(partial_receipt) is not dict
                    or (partial_receipt.get("ok") is True)
                    != (item.status == "observed")):
                _deny("dependency_recovery_untrusted_dispatch")
        step = by_id.get(item.step_id)
        if (step is None or item.tool_name != step.get("expected_tool")
                or item.tool_version != step.get("tool_version")):
            _deny("dependency_recovery_untrusted_dispatch")
        record = executions.get(str(item.step_id))
        attempt = locked_attempts[item.id]
        if item.status == "checkpointed":
            if (step.get("status") != "completed" or type(record) is not dict
                    or record.get("dispatch_id") != item.id
                    or record.get("tool_version") != item.tool_version
                    or record.get("tool_name") != item.tool_name
                    or attempt is None or attempt.status != "succeeded"
                    or attempt.conversation_id != conversation_id
                    or attempt.turn_id != item.turn_id
                    or attempt.step_id != item.step_id
                    or attempt.tool_name != item.tool_name
                    or attempt.tool_version != item.tool_version):
                _deny("dependency_recovery_untrusted_dispatch")
            try:
                args = json.loads(item.arguments_json)
                receipt = json.loads(item.receipt_json)
                projection = json.loads(attempt.result_projection)
            except (ValueError, TypeError):
                _deny("dependency_recovery_untrusted_dispatch")
            if (type(args) is not dict or type(receipt) is not dict
                    or type(projection) is not dict
                    or receipt.get("ok") is not True
                    or record.get("resolved_arguments_digest") != arguments_digest(args)
                    or attempt.arguments_digest != arguments_digest(args)
                    or projection != bounded_projection(item.tool_name, receipt)):
                _deny("dependency_recovery_untrusted_dispatch")
        elif (step.get("status") != "pending" or record is not None
              or attempt is not None):
            _deny("dependency_recovery_untrusted_dispatch")
    # Bind the approval to every persisted field that can affect evidence,
    # including Attempt metadata (not only its bounded result projection).
    # This is an evidence checksum, never a tool authorization token.
    evidence = []
    for item in dispatches:
        attempt = locked_attempts[item.id]
        attempt_meta = None if attempt is None else {
            "id": attempt.id,
            "conversation_id": attempt.conversation_id,
            "turn_id": attempt.turn_id,
            "step_id": attempt.step_id,
            "status": attempt.status,
            "tool_name": attempt.tool_name,
            "tool_version": attempt.tool_version,
            "arguments_digest": attempt.arguments_digest,
            "result_projection": attempt.result_projection,
            "error_code": attempt.error_code,
        }
        evidence.append({
            "id": item.id, "step": item.step_id, "status": item.status,
            "turn_id": item.turn_id,
            "tool_name": item.tool_name, "tool_version": item.tool_version,
            "worker_generation": item.worker_generation,
            "worker_token_digest": item.worker_token_digest,
            "arguments_sha256": hashlib.sha256(
                item.arguments_json.encode("utf-8")).hexdigest(),
            "receipt_sha256": hashlib.sha256(
                (item.receipt_json or "").encode("utf-8")).hexdigest(),
            "attempt_sha256": hashlib.sha256(
                json.dumps(attempt_meta, sort_keys=True, separators=(",", ":"))
                .encode("utf-8")).hexdigest(),
        })
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
    operator_access_verifier: Callable[[str, str, str, str], bool] | None = None,
    worker_termination_verifier: Callable[[str, str], bool] | None = None,
):
    """Fail-closed internal recovery requiring TWO external trust verifiers.

    operator_access_verifier(operator_id, owner_id, conversation_id, turn_id)
    and worker_termination_verifier(conversation_id, turn_id) must be trusted,
    independent implementations. Boolean flags and evidence hashes alone do
    NOT grant recovery. Neither verifier may derive authorization or proof
    solely from the caller's assertions. No public/model-callable endpoint.
    """
    if (operator_authorized is not True
            or worker_terminated_confirmed is not True
            or type(operator_id) is not str or not operator_id.strip()
            or not callable(operator_access_verifier)
            or not callable(worker_termination_verifier)
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
        # Check independently scoped operator authority AND irreversible
        # worker fencing only AFTER locking the Plan/Claim/Dispatch/Attempt
        # evidence being released. A liveness heartbeat check alone does not
        # prove that the original executor cannot resume.
        try:
            access_ok = operator_access_verifier(
                operator_id, owner_id, conversation_id, turn_id)
            termination_ok = worker_termination_verifier(conversation_id, turn_id)
        except Exception:
            _deny("dependency_recovery_external_verification_failed")
        if access_ok is not True:
            _deny("dependency_recovery_access_denied")
        if termination_ok is not True:
            _deny("dependency_recovery_worker_not_confirmed")
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
        # Invalidate the previous generation in this SAME transaction before
        # releasing the Claim. An old runner carrying its token cannot commit
        # a late Receipt or Checkpoint after recovery.
        revoke_dependency_fence_in_session(
            db, conversation_id=conversation_id, turn_id=turn_id)
        db.add(PlanRecoveryAudit(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            turn_id=turn_id, operator_id=operator_id.strip(),
            decision="dependency_orphan_released_no_replay",
            evidence_sha256=inspection.evidence_sha256,
            snapshot_sha256=inspection.plan_sha256))
        db.delete(claim)
        db.flush()
        return inspection
