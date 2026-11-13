"""S25-07 v2 integration: bounded second-step Dispatch -> Receipt -> Checkpoint.

Exactly one next step after a terminalized first v2 lookup, in a different
Host-authorized Turn. This DOES NOT invoke tools or attest external effects.
Every writer repeats locked physical history proof; an observed failure or
missing result never grants an automatic retry.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from sqlalchemy import select, update

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError, PINNED_TOOL_VERSION
from agent.primary_host_admission_v1 import PrimaryHostDecision
from agent.tool_registry import READ_REGISTRY, TextStatsArgs
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, ConversationPlan,
    DependencyV2StepReservation, Message, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, now,
)
from services.dependency_fencing import (
    DependencyFenceToken, assert_dependency_fence_in_session,
)
from services.dependency_revision_progress import _verify_revised_checkpoint_in_session
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_route_evidence import snapshot_digest


def _deny(code: str):
    raise DependencyPlanError(code)


def _sha(text: str):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(value, code):
    try:
        obj = json.loads(value)
    except (TypeError, ValueError):
        _deny(code)
    if type(obj) is not dict:
        _deny(code)
    return obj


def _bound_step2(
    db, *, settings, owner_id, conversation_id, turn_id, request_id,
    expected_raw, initial_turn_raw, fence, dispatch_id=None,
):
    """Same locks and proven prior history for all second-step writers."""
    if (type(settings) is not Settings
            or not all((
                settings.primary_route_v1_active,
                settings.s25_06_dependency_plan_enabled,
                settings.s25_07_revision_resume_prepare_enabled,
                settings.s25_07_revision_dispatch_intent_enabled,
                settings.s25_07_revision_receipt_checkpoint_enabled,
                settings.s25_07_revision_turn_terminal_enabled,
                settings.s25_07_revision_continuation_enabled,
            ))
            or settings.route_admission_mode != "guarded"
            or settings.understanding_strategy != "structured"
            or not all(type(v) is str and v.strip() for v in (
                owner_id, conversation_id, turn_id, request_id,
                expected_raw, initial_turn_raw))
            or expected_raw != initial_turn_raw
            or type(fence) is not DependencyFenceToken
            or fence.conversation_id != conversation_id
            or fence.turn_id != turn_id):
        _deny("dependency_v2_second_not_authorized")

    # The proof takes Plan→Claim→Fence→Dispatch→Attempt→Task locks.
    # A second Dispatch is ignored as a historical *completion* but is still
    # locked and constrained to its exact id before any mutation.
    proof = _verify_revised_checkpoint_in_session(
        db, owner_id=owner_id, conversation_id=conversation_id,
        expected_raw=expected_raw, continuation_turn_id=turn_id,
        pending_dispatch_id=dispatch_id)
    assert_dependency_fence_in_session(
        db, conversation_id=conversation_id, turn_id=turn_id, fence=fence)
    claim = db.get(PlanExecutionClaim, conversation_id)
    turn = db.get(Turn, turn_id)
    host = db.get(PrimaryRouteEvidence, turn_id)
    user = (db.get(Message, turn.user_message_id)
            if turn is not None and turn.user_message_id else None)
    if (claim is None or claim.turn_id != turn_id
            or claim.snapshot != expected_raw
            or turn is None or turn.conversation_id != conversation_id
            or turn.request_id != request_id or turn.state != "running"
            or turn.failure_code is not None
            or turn.assistant_message_id is not None
            or user is None or user.conversation_id != conversation_id
            or user.role != "user" or user.status != "completed"
            or not user.content.strip()
            or host is None or host.conversation_id != conversation_id
            or host.schema_version != "primary-route-v1"
            or host.route_kind != "plan_resume"
            or host.disposition != "allow"
            or host.admission_reason != "dependency_revision_resume_prepare_only"
            or host.approved_request_digest != _sha(user.content)
            or host.preturn_snapshot_digest != snapshot_digest(initial_turn_raw)):
        _deny("dependency_v2_second_host_denied")

    plan = _read(expected_raw, "dependency_v2_second_plan_denied")
    if (len(proof.remaining_step_ids) == 0
            or type(plan.get("steps")) is not list
            or type(plan.get("executions")) is not dict):
        _deny("dependency_v2_second_plan_denied")
    next_id = proof.remaining_step_ids[0]
    step = next((s for s in plan["steps"] if s.get("id") == next_id), None)
    preceding = next((s for s in plan["steps"]
                      if s.get("id") == proof.completed_v2_step_id), None)
    if (step is None or preceding is None
            or step.get("status") != "pending"
            or preceding.get("expected_tool") != "mcp_reference_lookup"
            or step.get("expected_tool") != "text_stats"
            or step.get("tool_version") != PINNED_TOOL_VERSION
            or READ_REGISTRY.version_for("text_stats") != PINNED_TOOL_VERSION):
        _deny("dependency_v2_second_tool_denied")
    bindings = step.get("argument_bindings")
    bind = bindings.get("text") if type(bindings) is dict else None
    if (type(bind) is not dict or bind.get("kind") != "observation_ref"
            or bind.get("field") != "definition"
            or bind.get("source_step_id") != proof.completed_v2_step_id
            or step.get("depends_on") != [proof.completed_v2_step_id]):
        _deny("dependency_v2_second_source_denied")
    source = db.get(PlanToolDispatch, proof.dispatch_id)
    if (source is None or source.plan_revision != 2
            or source.status != "checkpointed"
            or source.tool_name != "mcp_reference_lookup"):
        _deny("dependency_v2_second_source_denied")
    source_receipt = _read(source.receipt_json, "dependency_v2_second_source_denied")
    definition = source_receipt.get("definition")
    if (source_receipt.get("ok") is not True
            or type(definition) is not str or not definition
            or source_receipt.get("definition_availability") == "truncated"):
        _deny("dependency_v2_second_source_denied")
    try:
        args = TextStatsArgs.model_validate({"text": definition}).model_dump()
    except (ValueError, TypeError):
        _deny("dependency_v2_second_arguments_denied")
    if dispatch_id is not None:
        reservation = db.get(DependencyV2StepReservation,
                             (conversation_id, next_id))
        entry = db.get(PlanToolDispatch, dispatch_id)
        if (reservation is None or reservation.dispatch_id != dispatch_id
                or reservation.turn_id != turn_id
                or entry is None or entry.turn_id != turn_id
                or entry.conversation_id != conversation_id
                or entry.step_id != next_id
                or entry.plan_revision != 2
                or entry.worker_generation != fence.generation
                or entry.worker_token_digest != _sha(fence.secret)
                or entry.tool_name != "text_stats"
                or entry.tool_version != PINNED_TOOL_VERSION
                or _read(entry.arguments_json,
                         "dependency_v2_second_arguments_denied") != args):
            _deny("dependency_v2_second_dispatch_denied")
    else:
        if db.get(DependencyV2StepReservation, (conversation_id, next_id)) is not None:
            _deny("dependency_v2_second_duplicate")
        entry = None
    return plan, step, source, args, entry


def begin_second_revised_dispatch_in_session(
    db, *, settings: Settings, decision: PrimaryHostDecision,
    owner_id: str, conversation_id: str, turn_id: str, request_id: str,
    expected_raw: str, initial_turn_raw: str, fence: DependencyFenceToken,
) -> str:
    """Durable second-step intent; must COMMIT before any tool invocation."""
    if (type(decision) is not PrimaryHostDecision
            or decision.disposition != "allow"
            or decision.route_kind != "plan_resume"
            or decision.reason != "dependency_revision_resume_prepare_only"
            or decision.execution_mode != "read_only"
            or decision.plan_control != "resume"):
        _deny("dependency_v2_second_host_denied")
    plan, step, source, args, _ = _bound_step2(
        db, settings=settings, owner_id=owner_id,
        conversation_id=conversation_id, turn_id=turn_id,
        request_id=request_id, expected_raw=expected_raw,
        initial_turn_raw=initial_turn_raw, fence=fence)
    current_user = db.get(Message, db.get(Turn, turn_id).user_message_id)
    if decision.approved_request_digest != _sha(current_user.content):
        _deny("dependency_v2_second_host_denied")
    entry_id = str(uuid.uuid4())
    db.add(PlanToolDispatch(
        id=entry_id, conversation_id=conversation_id,
        turn_id=turn_id, step_id=step["id"], plan_revision=2,
        worker_generation=fence.generation,
        worker_token_digest=_sha(fence.secret),
        tool_name="text_stats", tool_version=PINNED_TOOL_VERSION,
        arguments_json=json.dumps(args, sort_keys=True, ensure_ascii=False),
        status="dispatch_unknown",
    ))
    db.flush()
    db.add(DependencyV2StepReservation(
        conversation_id=conversation_id, step_id=step["id"],
        turn_id=turn_id, dispatch_id=entry_id, created_at=now(),
    ))
    db.flush()
    return entry_id


def record_second_revised_receipt_in_session(
    db, *, settings: Settings, owner_id: str, conversation_id: str,
    turn_id: str, request_id: str, expected_raw: str,
    initial_turn_raw: str, fence: DependencyFenceToken,
    dispatch_id: str, observation: dict,
) -> dict:
    """Exactly one bounded synthetic/internal receipt; no retry on failure."""
    if (type(observation) is not dict or type(observation.get("ok")) is not bool
            or type(dispatch_id) is not str or not dispatch_id):
        _deny("dependency_v2_second_receipt_invalid")
    _, _, _, args, entry = _bound_step2(
        db, settings=settings, owner_id=owner_id,
        conversation_id=conversation_id, turn_id=turn_id,
        request_id=request_id, expected_raw=expected_raw,
        initial_turn_raw=initial_turn_raw, fence=fence, dispatch_id=dispatch_id)
    if entry.status != "dispatch_unknown" or entry.receipt_json is not None:
        _deny("dependency_v2_second_receipt_duplicate")
    receipt = bounded_projection("text_stats", observation)
    if receipt.get("ok") is True:
        if (type(receipt.get("characters")) is not int
                or type(receipt.get("words")) is not int
                or receipt["characters"] != len(args["text"])
                or receipt["words"] != len(args["text"].split())):
            _deny("dependency_v2_second_receipt_untrusted")
    entry.receipt_json = json.dumps(receipt, ensure_ascii=False)
    entry.status = "observed" if receipt["ok"] else "observed_failure"
    db.flush()
    return receipt


def checkpoint_second_revised_step_in_session(
    db, *, settings: Settings, owner_id: str, conversation_id: str,
    turn_id: str, request_id: str, expected_raw: str,
    initial_turn_raw: str, fence: DependencyFenceToken,
    dispatch_id: str,
) -> dict:
    """Atomic v2 step 2 checkpoint; does not terminate/release this Turn."""
    plan, step, source, args, entry = _bound_step2(
        db, settings=settings, owner_id=owner_id,
        conversation_id=conversation_id, turn_id=turn_id,
        request_id=request_id, expected_raw=expected_raw,
        initial_turn_raw=initial_turn_raw, fence=fence, dispatch_id=dispatch_id)
    if entry.status != "observed":
        _deny("dependency_v2_second_checkpoint_receipt_denied")
    receipt = _read(entry.receipt_json, "dependency_v2_second_checkpoint_receipt_denied")
    if (receipt.get("ok") is not True
            or bounded_projection("text_stats", receipt) != receipt
            or type(receipt.get("characters")) is not int
            or type(receipt.get("words")) is not int
            or receipt["characters"] != len(args["text"])
            or receipt["words"] != len(args["text"].split())):
        _deny("dependency_v2_second_checkpoint_receipt_denied")
    if db.get(AgentAttempt, dispatch_id) is not None:
        _deny("dependency_v2_second_checkpoint_duplicate")
    task = db.execute(select(AgentTaskState).where(
        AgentTaskState.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    if task is None or task.plan_revision != 2 or task.state != "partial":
        _deny("dependency_v2_second_task_untrusted")
    old_revision = task.revision
    new_plan = copy.deepcopy(plan)
    target = next(s for s in new_plan["steps"] if s["id"] == step["id"])
    target["status"] = "completed"
    new_plan["executions"][str(step["id"])] = {
        "dispatch_id": dispatch_id, "step_id": step["id"],
        "plan_revision": 2, "tool_name": entry.tool_name,
        "tool_version": entry.tool_version,
        "resolved_arguments_digest": arguments_digest(args),
        "worker_generation": entry.worker_generation,
        "worker_token_digest": entry.worker_token_digest,
        "source_evidence_refs": [{"source_dispatch_id": source.id}],
    }
    state = ("completed" if all(s["status"] == "completed"
             for s in new_plan["steps"]) else "partial")
    db.add(AgentAttempt(
        id=dispatch_id, conversation_id=conversation_id,
        turn_id=turn_id, run_id=turn_id, step_id=step["id"],
        tool_name="text_stats", tool_version=PINNED_TOOL_VERSION,
        arguments_digest=arguments_digest(args), status="succeeded",
        result_projection=json.dumps(receipt, ensure_ascii=False),
    ))
    db.flush()
    replacement = json.dumps(new_plan, ensure_ascii=False)
    updated = db.execute(update(ConversationPlan).where(
        ConversationPlan.conversation_id == conversation_id,
        ConversationPlan.snapshot == expected_raw,
    ).values(snapshot=replacement))
    if updated.rowcount != 1:
        _deny("dependency_v2_second_checkpoint_plan_conflict")
    task.revision = old_revision + 1
    task.state = state
    task.step_states = json.dumps([{
        "id": s["id"], "status": s["status"],
        "evidence_tool": s["expected_tool"] if s["status"] == "completed"
                         else None,
    } for s in new_plan["steps"]], ensure_ascii=False)
    task.updated_at = now()
    if state != "partial":
        db.add(AgentStateTransition(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            task_id=task.task_id, revision=task.revision,
            entity_type="task", step_id=None, from_state="partial",
            to_state=state, reason_code="task_status_changed", created_at=now(),
        ))
    db.add(AgentStateTransition(
        id=str(uuid.uuid4()), conversation_id=conversation_id,
        task_id=task.task_id, revision=task.revision,
        entity_type="step", step_id=step["id"], attempt_id=dispatch_id,
        from_state="pending", to_state="completed",
        reason_code="verified_step_completed", created_at=now(),
    ))
    db.get(PlanExecutionClaim, conversation_id).snapshot = replacement
    entry.status = "checkpointed"
    db.flush()
    return new_plan
