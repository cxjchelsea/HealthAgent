"""S25-07 v2 integration: guarded atomic Turn, Claim, and Fence close.

This does not dispatch, retry, execute tools, grant the next Turn, or expose
HTTP. Only a physically proven checkpoint may be closed, in the SAME DB
transaction which revokes its worker and releases the old Claim.
"""
from __future__ import annotations

import uuid

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    ConversationPlan, Message, PlanExecutionClaim, Turn,
)
from services.dependency_fencing import (
    DependencyFenceToken, assert_dependency_fence_in_session,
    revoke_dependency_fence_in_session,
)
from services.dependency_revision_progress import _verify_revised_checkpoint_in_session


def _deny(code: str):
    raise DependencyPlanError(code)


def finalize_revised_checkpoint_turn_in_session(
    db, *, settings: Settings, owner_id: str, conversation_id: str,
    expected_raw: str, turn_id: str, fence: DependencyFenceToken,
) -> str:
    """Single transaction finalization; the caller MUST commit/rollback.

    The physical progress verification and the termination/release share row
    locks. Never accept an earlier proof object as authority.
    """
    if (type(settings) is not Settings
            or not settings.primary_route_v1_active
            or not settings.s25_06_dependency_plan_enabled
            or not settings.s25_07_revision_resume_prepare_enabled
            or not settings.s25_07_revision_dispatch_intent_enabled
            or not settings.s25_07_revision_receipt_checkpoint_enabled
            or not settings.s25_07_revision_turn_terminal_enabled
            or settings.route_admission_mode != "guarded"
            or settings.understanding_strategy != "structured"
            or type(turn_id) is not str or not turn_id
            or type(fence) is not DependencyFenceToken
            or fence.turn_id != turn_id
            or fence.conversation_id != conversation_id):
        _deny("dependency_v2_turn_finalization_not_authorized")
    # The in-session proof already locked Plan → Claim → Fence →
    # Dispatch/Attempt → Task, and validated immutable v1/v2 provenance.
    progress = _verify_revised_checkpoint_in_session(
        db, owner_id=owner_id, conversation_id=conversation_id,
        expected_raw=expected_raw)
    assert_dependency_fence_in_session(
        db, conversation_id=conversation_id, turn_id=turn_id, fence=fence)
    claim = db.get(PlanExecutionClaim, conversation_id)
    current = db.get(ConversationPlan, conversation_id)
    turn = db.get(Turn, turn_id)
    if (claim is None or claim.turn_id != turn_id
            or claim.snapshot != expected_raw
            or current is None or current.snapshot != expected_raw
            or turn is None or turn.conversation_id != conversation_id
            or turn.state != "running"
            or progress.conversation_id != conversation_id):
        _deny("dependency_v2_turn_finalization_conflict")

    is_done = len(progress.remaining_step_ids) == 0
    status = "completed" if is_done else "partial"
    # A deliberately bounded, non-model message: the result is durable state,
    # not a claim of real external tool provenance. Future HTTP model answers
    # must be bound separately and must not rewrite this terminal evidence.
    response = ("本轮已保存经验证的执行结果，计划步骤已全部完成。"
                if is_done else
                "本轮已保存经验证的执行结果，计划仍有未完成步骤。")
    if turn.assistant_message_id is None:
        msg = Message(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            role="assistant", status=status, content=response)
        db.add(msg)
        db.flush()
        turn.assistant_message_id = msg.id
    else:
        msg = db.get(Message, turn.assistant_message_id)
        if (msg is None or msg.conversation_id != conversation_id
                or msg.role != "assistant"
                or msg.status not in ("running", "partial")):
            _deny("dependency_v2_turn_assistant_conflict")
        msg.content = response
        msg.status = status
    turn.state = status
    turn.failure_code = None
    turn.output_chars = len(response)
    # Fence first, then release Claim, atomically. A crashed worker cannot
    # write another receipt/checkpoint with its old generation after commit.
    revoke_dependency_fence_in_session(
        db, conversation_id=conversation_id, turn_id=turn_id)
    db.delete(claim)
    db.flush()
    return status
