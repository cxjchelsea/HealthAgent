"""Operator-only, non-replaying release of an abandoned revision2 Turn B.

An inspection digest alone does not authorize release. The caller MUST supply
independent, trusted operator-access and irreversible worker-termination
verifiers. Under the SAME DB transaction, re-inspect all durable v2 evidence,
record an immutable audit, revoke the worker fence and release the claim.
Never delete/reserve/retry/reclassify the original Dispatch, Receipt or Attempt.
Not exposed through HTTP, tools, or model-controlled Agent paths.
"""
from __future__ import annotations

import uuid
from typing import Callable

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    Message, PlanExecutionClaim, PlanRecoveryAudit, Turn,
)
from services.dependency_fencing import revoke_dependency_fence_in_session
from services.dependency_revision_recovery_inspect import (
    _inspect_revised_second_in_session, RevisedSecondTurnInspection,
)


def _deny(reason: str):
    raise DependencyPlanError(reason)


def reconcile_revised_second_orphan(
    session_factory, *, owner_id: str, conversation_id: str,
    turn_id: str, expected_raw: str, operator_id: str,
    expected_plan_sha256: str, expected_evidence_sha256: str,
    operator_authorized: bool = False,
    worker_terminated_confirmed: bool = False,
    operator_access_verifier: Callable[[str, str, str, str], bool] | None = None,
    worker_termination_verifier: Callable[[str, str], bool] | None = None,
) -> RevisedSecondTurnInspection:
    """Explicit operator release. A successful uncheckpointed Receipt is held.

    A crashed worker can no longer append a late Receipt/Checkpoint once
    the transaction commits. The pending step's immutable reservation remains
    present, so a later Turn cannot silently re-dispatch the unknown effect.
    """
    if (operator_authorized is not True
            or worker_terminated_confirmed is not True
            or type(operator_id) is not str or not operator_id.strip()
            or not callable(operator_access_verifier)
            or not callable(worker_termination_verifier)
            or not all(type(x) is str and len(x) == 64
                       and all(c in "0123456789abcdef" for c in x)
                       for x in (expected_plan_sha256, expected_evidence_sha256))):
        _deny("dependency_v2_recovery_operator_required")
    with session_factory.begin() as db:
        inspection = _inspect_revised_second_in_session(
            db, owner_id=owner_id, conversation_id=conversation_id,
            turn_id=turn_id, expected_raw=expected_raw)
        if (inspection.plan_sha256 != expected_plan_sha256
                or inspection.evidence_sha256 != expected_evidence_sha256):
            _deny("dependency_v2_recovery_evidence_changed")
        # A durable SUCCESS receipt is not unknown. Reconcile via a separately
        # verified Checkpoint; do not release this claim as an orphan.
        if inspection.classification == "verified_receipt_checkpoint_required":
            _deny("dependency_v2_recovery_checkpoint_required")
        if inspection.classification not in (
            "reserved_no_safe_replay", "uncertain_no_replay",
            "verified_failure_no_replay",
        ):
            _deny("dependency_v2_recovery_state_untrusted")
        try:
            access = operator_access_verifier(
                operator_id, owner_id, conversation_id, turn_id)
            stopped = worker_termination_verifier(conversation_id, turn_id)
        except Exception:
            _deny("dependency_v2_recovery_external_verification_failed")
        if access is not True:
            _deny("dependency_v2_recovery_access_denied")
        if stopped is not True:
            _deny("dependency_v2_recovery_worker_not_confirmed")
        claim = db.get(PlanExecutionClaim, conversation_id)
        turn = db.get(Turn, turn_id)
        if (claim is None or claim.turn_id != turn_id
                or claim.snapshot != expected_raw or turn is None
                or turn.state != "running"
                or turn.assistant_message_id is not None):
            _deny("dependency_v2_recovery_claim_changed")
        # A partial Turn is NOT a successful execution or permission to retry.
        turn.state = "partial"
        turn.failure_code = (
            "dependency_verified_failure"
            if inspection.classification == "verified_failure_no_replay"
            else "dependency_reconciliation_required")
        answer = Message(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            role="assistant", status="partial",
            content=("工具失败证据已保留，原步骤未完成，不会自动重试。"
                     if inspection.classification == "verified_failure_no_replay"
                     else "工具执行结果待人工核对，原步骤未完成，不会自动重试。"))
        db.add(answer)
        db.flush()
        turn.assistant_message_id = answer.id
        turn.output_chars = len(answer.content)
        revoke_dependency_fence_in_session(
            db, conversation_id=conversation_id, turn_id=turn_id)
        db.add(PlanRecoveryAudit(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            turn_id=turn_id, operator_id=operator_id.strip(),
            decision="dependency_v2_orphan_released_no_replay",
            evidence_sha256=inspection.evidence_sha256,
            snapshot_sha256=inspection.plan_sha256))
        db.delete(claim)
        db.flush()
        return inspection
