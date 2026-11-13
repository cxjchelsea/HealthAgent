"""S25-07 integrated v2 continuation: guarded next-Turn Claim admission.

Only creates a durable Claim for a new Host-authorized Turn after verified
terminal history. No tool invocation, Dispatch, Receipt, or automatic retry.
A fresh Fence token must be acquired separately before any possible execution.
"""
from __future__ import annotations

import hashlib
from sqlalchemy.exc import IntegrityError

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError
from agent.primary_host_admission_v1 import PrimaryHostDecision
from services.db import (
    ConversationPlan, Message, PlanExecutionClaim, PrimaryRouteEvidence, Turn,
)
from services.dependency_revision_progress import _verify_revised_checkpoint_in_session
from services.primary_route_evidence import snapshot_digest


def _deny(reason: str):
    raise DependencyPlanError(reason)


def admit_revised_continuation_claim_in_session(
    db, *, settings: Settings, decision: PrimaryHostDecision,
    owner_id: str, conversation_id: str, turn_id: str, request_id: str,
    expected_raw: str, initial_turn_raw: str,
) -> int:
    """One atomic proof + new claim. Returns pending step ID (not a permit).

    Requires exactly the first terminalized v2 step: this staged milestone
    intentionally rejects deeper progressed history until the generalized
    multi-step verifier exists.
    """
    if (type(settings) is not Settings
            or not settings.primary_route_v1_active
            or not settings.s25_06_dependency_plan_enabled
            or not settings.s25_07_revision_resume_prepare_enabled
            or not settings.s25_07_revision_dispatch_intent_enabled
            or not settings.s25_07_revision_receipt_checkpoint_enabled
            or not settings.s25_07_revision_turn_terminal_enabled
            or not settings.s25_07_revision_continuation_enabled
            or settings.route_admission_mode != "guarded"
            or settings.understanding_strategy != "structured"
            or type(decision) is not PrimaryHostDecision
            or decision.route_kind != "plan_resume"
            or decision.disposition != "allow"
            or decision.reason != "dependency_revision_resume_prepare_only"
            or decision.execution_mode != "read_only"
            or decision.plan_control != "resume"
            or not all(type(v) is str and v.strip() for v in (
                owner_id, conversation_id, turn_id, request_id,
                expected_raw, initial_turn_raw))
            or initial_turn_raw != expected_raw):
        _deny("dependency_v2_continuation_not_authorized")

    # In the SAME transaction: lock Plan / absent Claim / terminal Fence /
    # historical Dispatch + Attempt + Task before consuming new Host approval.
    verified = _verify_revised_checkpoint_in_session(
        db, owner_id=owner_id, conversation_id=conversation_id,
        expected_raw=expected_raw, terminal_history=True)
    if not verified.remaining_step_ids:
        _deny("dependency_v2_continuation_plan_completed")
    if (db.get(PlanExecutionClaim, conversation_id) is not None
            or db.get(ConversationPlan, conversation_id).snapshot != expected_raw):
        _deny("dependency_v2_continuation_claim_conflict")
    turn = db.get(Turn, turn_id)
    host = db.get(PrimaryRouteEvidence, turn_id)
    user = (db.get(Message, turn.user_message_id)
            if turn is not None and turn.user_message_id else None)
    if (turn is None or turn.conversation_id != conversation_id
            or turn.request_id != request_id
            or turn.state != "running" or turn.failure_code is not None
            or turn.assistant_message_id is not None
            or user is None or user.conversation_id != conversation_id
            or user.role != "user" or user.status != "completed"
            or not user.content.strip()
            or host is None or host.conversation_id != conversation_id
            or host.schema_version != "primary-route-v1"
            or host.route_kind != "plan_resume"
            or host.admission_reason != "dependency_revision_resume_prepare_only"
            or host.disposition != "allow"
            or host.approved_request_digest is None
            or host.approved_request_digest
                != hashlib.sha256(user.content.encode("utf-8")).hexdigest()
            or decision.approved_request_digest != host.approved_request_digest
            or host.preturn_snapshot_digest != snapshot_digest(initial_turn_raw)):
        _deny("dependency_v2_continuation_host_untrusted")
    # An earlier terminal Turn and the current Turn are separate identities.
    from services.db import DependencyWorkerFence
    prior = db.get(DependencyWorkerFence, conversation_id)
    if prior is None or prior.turn_id == turn_id:
        _deny("dependency_v2_continuation_same_turn")
    db.add(PlanExecutionClaim(
        conversation_id=conversation_id, turn_id=turn_id,
        snapshot=expected_raw))
    db.flush()
    return verified.remaining_step_ids[0]
