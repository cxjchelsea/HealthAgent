"""S25-07 B3c-3: bounded, internal-only v2 receipt and atomic checkpoint.

No tool invocation, retry, API exposure, or cross-Turn resume. A receipt is
an observation supplied by a trusted future runner, NOT proof of external
exactly-once effects. Receipt and checkpoint are distinct guarded transactions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from sqlalchemy import select, update

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError
from agent.dependency_revision_host import PreparedRevisedRead
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyRevisionAnchor, DependencyV2StepReservation,
    Message, PlanExecutionClaim, PlanToolDispatch, PrimaryRouteEvidence, Turn, now,
)
from services.dependency_fencing import (
    DependencyFenceToken, assert_dependency_fence_in_session,
)
from services.dependency_revision_dispatch import _verify_inherited
from services.dependency_revision_fingerprint import revision_fingerprint
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_route_evidence import snapshot_digest


def _deny(code):
    raise DependencyPlanError(code)


def _sha(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse(raw, code):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        _deny(code)
    if type(value) is not dict:
        _deny(code)
    return value


def _bound_rows(db, *, settings, binding, expected_raw, fence, dispatch_id,
                initial_turn_raw):
    """Shared locked authority for both v2 receipt and checkpoint.

    All writers use Plan -> Claim -> Fence -> Dispatch -> Attempt -> Task.
    This function intentionally supports only the *first* pristine v2 step;
    progressed resume requires the B3c-4 physical history verifier.
    """
    if (type(settings) is not Settings
            or not settings.s25_06_dependency_plan_enabled
            or not settings.s25_07_revision_resume_prepare_enabled
            or not settings.s25_07_revision_dispatch_intent_enabled
            or not settings.s25_07_revision_receipt_checkpoint_enabled
            or not settings.primary_route_v1_active
            or settings.route_admission_mode != "guarded"
            or settings.understanding_strategy != "structured"
            or type(binding) is not PreparedRevisedRead
            or binding.plan_revision != 2
            or type(expected_raw) is not str
            or initial_turn_raw != expected_raw
            or binding.checkpoint_digest != _sha(expected_raw)
            or arguments_digest(binding.arguments) != binding.arguments_digest
            or type(fence) is not DependencyFenceToken
            or type(dispatch_id) is not str or not dispatch_id):
        _deny("dependency_v2_result_not_authorized")

    plan = db.execute(select(ConversationPlan).where(
        ConversationPlan.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    claim = db.execute(select(PlanExecutionClaim).where(
        PlanExecutionClaim.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    assert_dependency_fence_in_session(
        db, conversation_id=binding.conversation_id,
        turn_id=binding.turn_id, fence=fence)
    dispatch = db.execute(select(PlanToolDispatch).where(
        PlanToolDispatch.id == dispatch_id
    ).with_for_update()).scalar_one_or_none()
    owner = db.get(Conversation, binding.conversation_id)
    turn = db.get(Turn, binding.turn_id)
    user = (db.execute(select(Message).where(
        Message.id == turn.user_message_id
    ).with_for_update()).scalar_one_or_none()
            if turn is not None and turn.user_message_id else None)
    host = db.get(PrimaryRouteEvidence, binding.turn_id)
    anchor = db.get(DependencyRevisionAnchor, binding.conversation_id)
    reservation = db.get(DependencyV2StepReservation,
                         (binding.conversation_id, binding.step_id))

    if (plan is None or plan.snapshot != expected_raw
            or claim is None or claim.turn_id != binding.turn_id
            or claim.snapshot != expected_raw
            or owner is None or owner.owner_id != binding.owner_id
            or turn is None or turn.conversation_id != binding.conversation_id
            or turn.request_id != binding.request_id or turn.state != "running"
            or user is None or user.role != "user"
            or user.conversation_id != binding.conversation_id
            or user.status != "completed"
            or host is None or host.conversation_id != binding.conversation_id
            or host.schema_version != "primary-route-v1"
            or host.route_kind != "plan_resume" or host.disposition != "allow"
            or host.admission_reason != "dependency_revision_resume_prepare_only"
            or host.approved_request_digest != _sha(user.content)
            or host.preturn_snapshot_digest != snapshot_digest(initial_turn_raw)
            or anchor is None
            or anchor.revision_turn_id != binding.approved_revision_turn_id
            or reservation is None or reservation.dispatch_id != dispatch_id
            or reservation.turn_id != binding.turn_id
            or dispatch is None or dispatch.id != reservation.dispatch_id
            or dispatch.conversation_id != binding.conversation_id
            or dispatch.turn_id != binding.turn_id
            or dispatch.step_id != binding.step_id
            or dispatch.plan_revision != 2
            or dispatch.tool_name != binding.tool_name
            or dispatch.tool_version != binding.tool_version
            or dispatch.worker_generation != fence.generation
            or dispatch.worker_token_digest != _sha(fence.secret)):
        _deny("dependency_v2_result_identity_denied")

    original_approval = db.get(PrimaryRouteEvidence, anchor.revision_turn_id)
    revision_turn = db.get(Turn, anchor.revision_turn_id)
    revision_user = (db.get(Message, revision_turn.user_message_id)
                     if revision_turn is not None and revision_turn.user_message_id
                     else None)
    revision_assistant = (db.get(Message, revision_turn.assistant_message_id)
                          if revision_turn is not None and revision_turn.assistant_message_id
                          else None)
    if (original_approval is None
            or original_approval.conversation_id != binding.conversation_id
            or original_approval.schema_version != "primary-route-v1"
            or original_approval.route_kind != "plan_revise"
            or original_approval.disposition != "allow"
            or original_approval.admission_reason != "dependency_revision_candidate"
            or original_approval.approved_revision_digest != anchor.approved_digest
            or revision_turn is None
            or revision_turn.conversation_id != binding.conversation_id
            or revision_turn.state != "partial"
            or revision_turn.failure_code is not None
            or revision_user is None
            or revision_user.conversation_id != binding.conversation_id
            or revision_user.role != "user"
            or revision_user.status != "completed"
            or not revision_user.content.strip()
            or revision_assistant is None
            or revision_assistant.conversation_id != binding.conversation_id
            or revision_assistant.role != "assistant"
            or revision_assistant.status != "partial"):
        _deny("dependency_v2_result_origin_denied")
    checkpoint = _parse(expected_raw, "dependency_v2_result_invalid_plan")
    original = _parse(anchor.approved_candidate_json, "dependency_v2_result_origin_denied")
    try:
        origin_refs = json.loads(anchor.origin_refs_json)
    except (ValueError, TypeError):
        _deny("dependency_v2_result_origin_denied")
    lineage = checkpoint.get("lineage")
    if (type(lineage) is not dict
            or checkpoint != original
            or checkpoint.get("revision") != 2
            or checkpoint.get("plan_executor_version")
                != "dependency-plan-read-replan-v1"
            or revision_fingerprint(original) != anchor.approved_digest
            or lineage.get("parent_snapshot_sha256")
                != anchor.parent_snapshot_sha256
            or lineage.get("origin_refs") != origin_refs):
        _deny("dependency_v2_result_origin_denied")

    steps = checkpoint.get("steps")
    executions = checkpoint.get("executions")
    if (type(steps) is not list or type(executions) is not dict
            or not steps or any(type(s) is not dict for s in steps)):
        _deny("dependency_v2_result_invalid_plan")
    pending = [s for s in steps if s.get("status") == "pending"]
    if (not pending or pending[0].get("id") != binding.step_id
            or binding.step_id in {s.get("id") for s in steps
                                   if s.get("status") == "completed"}
            or str(binding.step_id) in executions):
        _deny("dependency_v2_result_step_denied")
    step = pending[0]
    if (step.get("expected_tool") != binding.tool_name
            or step.get("tool_version") != binding.tool_version):
        _deny("dependency_v2_result_step_denied")
    args = _parse(dispatch.arguments_json, "dependency_v2_result_arguments_denied")
    if args != binding.arguments or arguments_digest(args) != binding.arguments_digest:
        _deny("dependency_v2_result_arguments_denied")
    # Physical origin is reverified under locks. The only v2 Dispatch row must
    # be the selected one; all other Dispatches must be signed-off v1 prefix.
    all_dispatch = db.scalars(select(PlanToolDispatch).where(
        PlanToolDispatch.conversation_id == binding.conversation_id
    ).order_by(PlanToolDispatch.step_id, PlanToolDispatch.id)
        .with_for_update()).all()
    v2 = [d for d in all_dispatch if d.plan_revision == 2]
    if len(v2) != 1 or v2[0].id != dispatch_id:
        _deny("dependency_v2_result_dispatch_conflict")
    _verify_inherited(db, conversation_id=binding.conversation_id,
                      checkpoint=checkpoint,
                      dispatches=[d for d in all_dispatch if d.id != dispatch_id])
    task = db.execute(select(AgentTaskState).where(
        AgentTaskState.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    expected_projection = [{
        "id": s["id"], "status": s["status"],
        "evidence_tool": s["expected_tool"] if s["status"] == "completed" else None,
    } for s in steps]
    if task is None or task.plan_revision != 2:
        _deny("dependency_v2_result_task_conflict")
    try:
        actual_projection = json.loads(task.step_states)
    except (ValueError, TypeError):
        _deny("dependency_v2_result_task_conflict")
    origin_transitions = db.scalars(select(AgentStateTransition).where(
        AgentStateTransition.conversation_id == binding.conversation_id,
        AgentStateTransition.reason_code == "dependency_plan_revision_saved",
    )).all()
    if (actual_projection != expected_projection
            or task.state != ("partial" if any(
                s["status"] == "completed" for s in steps) else "planned")
            or len(origin_transitions) != 1
            or origin_transitions[0].task_id != task.task_id
            or origin_transitions[0].revision != task.revision
            or origin_transitions[0].entity_type != "task"
            or origin_transitions[0].step_id is not None
            or origin_transitions[0].attempt_id is not None
            or origin_transitions[0].from_state
                not in (None, "planned", "partial", "blocked")
            or origin_transitions[0].to_state != task.state):
        _deny("dependency_v2_result_task_conflict")
    return checkpoint, dispatch, task, step


def record_revised_receipt_in_session(
    db, *, settings: Settings, binding: PreparedRevisedRead,
    expected_raw: str, initial_turn_raw: str,
    fence: DependencyFenceToken, dispatch_id: str, observation: dict,
) -> dict:
    """Record exactly one bounded observation for a committed v2 intent.

    A failure is observed_failure, never checkpointed or retried. A successful
    receipt is not a checkpoint or independent proof of a real invocation.
    """
    if type(observation) is not dict or type(observation.get("ok")) is not bool:
        _deny("dependency_v2_receipt_invalid")
    _, dispatch, _, _ = _bound_rows(
        db, settings=settings, binding=binding, expected_raw=expected_raw,
        initial_turn_raw=initial_turn_raw, fence=fence, dispatch_id=dispatch_id)
    if dispatch.status != "dispatch_unknown" or dispatch.receipt_json is not None:
        _deny("dependency_v2_receipt_duplicate")
    projected = bounded_projection(binding.tool_name, observation)
    if type(projected) is not dict or type(projected.get("ok")) is not bool:
        _deny("dependency_v2_receipt_invalid")
    dispatch.receipt_json = json.dumps(projected, ensure_ascii=False)
    dispatch.status = "observed" if projected["ok"] else "observed_failure"
    db.flush()
    return projected


def checkpoint_revised_step_in_session(
    db, *, settings: Settings, binding: PreparedRevisedRead,
    expected_raw: str, initial_turn_raw: str,
    fence: DependencyFenceToken, dispatch_id: str,
) -> dict:
    """Atomically bind a successful observed v2 receipt to plan + task.

    Only the first pending v2 step is supported. The caller owns transaction
    commit; NO tool invocation or next-Turn authority is created here.
    """
    checkpoint, dispatch, task, step = _bound_rows(
        db, settings=settings, binding=binding, expected_raw=expected_raw,
        initial_turn_raw=initial_turn_raw, fence=fence, dispatch_id=dispatch_id)
    if dispatch.status != "observed":
        _deny("dependency_v2_checkpoint_receipt_denied")
    receipt = _parse(dispatch.receipt_json, "dependency_v2_checkpoint_receipt_denied")
    if receipt.get("ok") is not True or bounded_projection(
            binding.tool_name, receipt) != receipt:
        _deny("dependency_v2_checkpoint_receipt_denied")
    if db.get(AgentAttempt, dispatch_id) is not None:
        _deny("dependency_v2_checkpoint_duplicate")
    args = _parse(dispatch.arguments_json, "dependency_v2_checkpoint_arguments_denied")
    if binding.tool_name == "mcp_reference_lookup":
        template = step["argument_bindings"]["term"]
        if (template.get("kind") != "literal"
                or step.get("depends_on") != []
                or args != {"term": template.get("value")}
                or binding.inherited_source_dispatch_id is not None
                or type(receipt.get("definition")) is not str
                or not receipt["definition"]
                or receipt.get("definition_availability") == "truncated"):
            _deny("dependency_v2_checkpoint_untrusted_observation")
    elif binding.tool_name == "text_stats":
        template = step["argument_bindings"]["text"]
        source_id = template.get("source_step_id")
        source = (db.get(PlanToolDispatch, binding.inherited_source_dispatch_id)
                  if binding.inherited_source_dispatch_id else None)
        if (template.get("kind") != "observation_ref"
                or template.get("field") != "definition"
                or source_id not in step.get("depends_on", [])
                or source is None or source.step_id != source_id
                or source.plan_revision not in (None, 1)
                or source.status != "checkpointed"
                or source.tool_name != "mcp_reference_lookup"
                or source.id != checkpoint["executions"][str(source_id)]["dispatch_id"]
                or type(receipt.get("characters")) is not int
                or type(receipt.get("words")) is not int
                or receipt["characters"] != len(args["text"])
                or receipt["words"] != len(args["text"].split())):
            _deny("dependency_v2_checkpoint_untrusted_observation")
        old = _parse(source.receipt_json, "dependency_v2_checkpoint_untrusted_observation")
        if old.get("definition") != args.get("text"):
            _deny("dependency_v2_checkpoint_untrusted_observation")
    else:
        _deny("dependency_v2_checkpoint_tool_denied")

    new_plan = copy.deepcopy(checkpoint)
    for candidate in new_plan["steps"]:
        if candidate["id"] == binding.step_id:
            candidate["status"] = "completed"
            break
    new_plan["executions"][str(binding.step_id)] = {
        "dispatch_id": dispatch_id, "step_id": binding.step_id,
        "plan_revision": 2,
        "tool_name": binding.tool_name, "tool_version": binding.tool_version,
        "resolved_arguments_digest": binding.arguments_digest,
        "source_evidence_refs": (
            [{"source_dispatch_id": binding.inherited_source_dispatch_id}]
            if binding.inherited_source_dispatch_id else []),
    }
    next_task_state = ("completed" if all(
        s["status"] == "completed" for s in new_plan["steps"]) else "partial")
    next_revision = task.revision + 1
    updated_steps = [{
        "id": s["id"], "status": s["status"],
        "evidence_tool": s["expected_tool"] if s["status"] == "completed" else None,
    } for s in new_plan["steps"]]

    # A completed Attempt, Task projections, Plan CAS, Claim and Dispatch
    # finalization are ONE transaction. A crash rolls back all of them.
    db.add(AgentAttempt(
        id=dispatch_id, conversation_id=binding.conversation_id,
        turn_id=binding.turn_id, run_id=binding.turn_id,
        step_id=binding.step_id, tool_name=binding.tool_name,
        tool_version=binding.tool_version,
        arguments_digest=binding.arguments_digest,
        status="succeeded",
        result_projection=json.dumps(receipt, ensure_ascii=False),
    ))
    db.flush()
    changed = db.execute(update(ConversationPlan).where(
        ConversationPlan.conversation_id == binding.conversation_id,
        ConversationPlan.snapshot == expected_raw,
    ).values(snapshot=json.dumps(new_plan, ensure_ascii=False)))
    if changed.rowcount != 1:
        _deny("dependency_v2_checkpoint_plan_conflict")
    old_task_state = task.state
    task.revision = next_revision
    task.state = next_task_state
    task.plan_revision = 2
    task.step_states = json.dumps(updated_steps, ensure_ascii=False)
    task.updated_at = now()
    if old_task_state != next_task_state:
        db.add(AgentStateTransition(
            id=str(uuid.uuid4()), conversation_id=binding.conversation_id,
            task_id=task.task_id, revision=next_revision,
            entity_type="task", step_id=None,
            from_state=old_task_state, to_state=next_task_state,
            reason_code="task_status_changed", created_at=now(),
        ))
    db.add(AgentStateTransition(
        id=str(uuid.uuid4()), conversation_id=binding.conversation_id,
        task_id=task.task_id, revision=next_revision,
        entity_type="step", step_id=binding.step_id, attempt_id=dispatch_id,
        from_state="pending", to_state="completed",
        reason_code="verified_step_completed", created_at=now(),
    ))
    db.get(PlanExecutionClaim, binding.conversation_id).snapshot = json.dumps(
        new_plan, ensure_ascii=False)
    dispatch.status = "checkpointed"
    db.flush()
    return new_plan
