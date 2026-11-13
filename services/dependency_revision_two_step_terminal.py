"""S25-07 integrated v2: final attestation for a bounded two-Turn chain.

Only a pristine TWO-STEP v2 candidate with no inherited v1 prefix, of
mcp_reference_lookup → text_stats, is supported. The proof is re-evaluated
under the SAME transaction that terminalizes Turn B. This is not a tool
invocation attestation, next-Turn grant, general DAG proof, or recovery permit.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from sqlalchemy import select

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError, PINNED_TOOL_VERSION
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyRevisionAnchor, DependencyV2StepReservation,
    DependencyWorkerFence, Message, PlanExecutionClaim, PlanToolDispatch,
    PrimaryRouteEvidence, Turn, now,
)
from services.dependency_fencing import (
    DependencyFenceToken, assert_dependency_fence_in_session,
    revoke_dependency_fence_in_session,
)
from services.dependency_revision_fingerprint import revision_fingerprint
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_route_evidence import snapshot_digest


def _deny():
    raise DependencyPlanError("dependency_v2_two_step_terminal_untrusted")


def _obj(raw):
    try:
        item = json.loads(raw)
    except (ValueError, TypeError):
        _deny()
    if type(item) is not dict:
        _deny()
    return item


def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verified_turn(db, *, cid, tid, raw, terminal, route_kind,
                   reason, expected_request=None):
    turn = db.get(Turn, tid)
    host = db.get(PrimaryRouteEvidence, tid)
    user = (db.get(Message, turn.user_message_id)
            if turn is not None and turn.user_message_id else None)
    if (turn is None or turn.conversation_id != cid
            or turn.failure_code is not None
            or not turn.request_id
            or (turn.state not in ("partial", "completed")
                if terminal else turn.state != "running")
            or (not terminal and turn.assistant_message_id is not None)
            or host is None or host.conversation_id != cid
            or host.schema_version != "primary-route-v1"
            or host.route_kind != route_kind
            or host.disposition != "allow"
            or host.admission_reason != reason
            or host.preturn_snapshot_digest != snapshot_digest(raw)
            or user is None or user.conversation_id != cid
            or user.role != "user" or user.status != "completed"
            or not user.content.strip()
            or host.approved_request_digest != _hash(user.content)
            or (expected_request is not None and turn.request_id != expected_request)):
        _deny()
    if terminal:
        assistant = (db.get(Message, turn.assistant_message_id)
                     if turn.assistant_message_id else None)
        if (turn.state != "partial" or assistant is None
                or assistant.conversation_id != cid
                or assistant.role != "assistant" or assistant.status != "partial"
                or not assistant.content
                or turn.output_chars != len(assistant.content)):
            _deny()
    return turn


def _verify_two_step_chain_in_session(
    db, *, owner_id: str, conversation_id: str,
    expected_raw: str, turn_id: str, request_id: str,
    fence: DependencyFenceToken,
):
    if not all(type(x) is str and x for x in (
            owner_id, conversation_id, expected_raw, turn_id, request_id)):
        _deny()
    owner = db.get(Conversation, conversation_id)
    plan_row = db.execute(select(ConversationPlan).where(
        ConversationPlan.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    claim = db.execute(select(PlanExecutionClaim).where(
        PlanExecutionClaim.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    worker = db.execute(select(DependencyWorkerFence).where(
        DependencyWorkerFence.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    if (owner is None or owner.owner_id != owner_id
            or plan_row is None or plan_row.snapshot != expected_raw
            or claim is None or claim.turn_id != turn_id
            or claim.snapshot != expected_raw
            or worker is None or worker.turn_id != turn_id
            or worker.state != "active" or worker.generation < 2):
        _deny()
    assert_dependency_fence_in_session(
        db, conversation_id=conversation_id, turn_id=turn_id, fence=fence)
    anchor = db.get(DependencyRevisionAnchor, conversation_id)
    if anchor is None:
        _deny()
    pristine = _obj(anchor.approved_candidate_json)
    evolved = _obj(expected_raw)
    if (revision_fingerprint(pristine) != anchor.approved_digest
            or pristine.get("revision") != 2
            or pristine.get("plan_executor_version") != "dependency-plan-read-replan-v1"
            or type(pristine.get("lineage")) is not dict
            or pristine["lineage"].get("parent_snapshot_sha256")
                != anchor.parent_snapshot_sha256
            or type(pristine.get("executions")) is not dict
            or pristine["executions"] != {}
            or type(pristine.get("steps")) is not list
            or len(pristine["steps"]) != 2
            or any(type(s) is not dict or s.get("status") != "pending"
                   for s in pristine["steps"])
            or pristine["lineage"].get("preserved_step_ids") != []
            or pristine["lineage"].get("origin_refs") != []):
        _deny()
    try:
        origin_refs = json.loads(anchor.origin_refs_json)
    except (TypeError, ValueError):
        _deny()
    if origin_refs != []:
        _deny()
    first_step, second_step = pristine["steps"]
    first_id, second_id = first_step.get("id"), second_step.get("id")
    if (type(first_id) is not int or type(second_id) is not int
            or first_id >= second_id
            or first_step.get("expected_tool") != "mcp_reference_lookup"
            or first_step.get("tool_version") != PINNED_TOOL_VERSION
            or first_step.get("depends_on") != []
            or second_step.get("expected_tool") != "text_stats"
            or second_step.get("tool_version") != PINNED_TOOL_VERSION
            or second_step.get("depends_on") != [first_id]
            or first_step.get("argument_bindings", {}).get("term", {}).get("kind")
                != "literal"
            or second_step.get("argument_bindings", {}).get("text") != {
                "kind": "observation_ref", "source_step_id": first_id,
                "field": "definition"}):
        _deny()

    approval = db.get(PrimaryRouteEvidence, anchor.revision_turn_id)
    revision = db.get(Turn, anchor.revision_turn_id)
    revision_user = (db.get(Message, revision.user_message_id)
                     if revision is not None and revision.user_message_id else None)
    revision_assistant = (db.get(Message, revision.assistant_message_id)
                          if revision is not None and revision.assistant_message_id else None)
    if (approval is None or approval.conversation_id != conversation_id
            or approval.schema_version != "primary-route-v1"
            or approval.route_kind != "plan_revise"
            or approval.disposition != "allow"
            or approval.admission_reason != "dependency_revision_candidate"
            or approval.approved_revision_digest != anchor.approved_digest
            or revision is None or revision.conversation_id != conversation_id
            or revision.state != "partial" or revision.failure_code is not None
            or revision_user is None or revision_user.conversation_id != conversation_id
            or revision_user.role != "user" or revision_user.status != "completed"
            or not revision_user.content.strip()
            or revision_assistant is None
            or revision_assistant.conversation_id != conversation_id
            or revision_assistant.role != "assistant"
            or revision_assistant.status != "partial"):
        _deny()

    dispatches = db.scalars(select(PlanToolDispatch).where(
        PlanToolDispatch.conversation_id == conversation_id
    ).order_by(PlanToolDispatch.step_id, PlanToolDispatch.id)
        .with_for_update()).all()
    if (len(dispatches) != 2
            or any(d.plan_revision != 2 or d.status != "checkpointed"
                   for d in dispatches)
            or [d.step_id for d in dispatches] != [first_id, second_id]):
        _deny()
    first, second = dispatches
    reservations = db.scalars(select(DependencyV2StepReservation).where(
        DependencyV2StepReservation.conversation_id == conversation_id
    ).order_by(DependencyV2StepReservation.step_id)
        .with_for_update()).all()
    if (len(reservations) != 2
            or [r.step_id for r in reservations] != [first_id, second_id]
            or reservations[0].dispatch_id != first.id
            or reservations[1].dispatch_id != second.id
            or reservations[0].turn_id != first.turn_id
            or reservations[1].turn_id != second.turn_id
            or first.turn_id == second.turn_id
            or second.turn_id != turn_id
            or first.worker_generation != worker.generation - 1
            or second.worker_generation != worker.generation
            or second.worker_token_digest != worker.token_digest
            or first.tool_name != "mcp_reference_lookup"
            or second.tool_name != "text_stats"
            or first.tool_version != PINNED_TOOL_VERSION
            or second.tool_version != PINNED_TOOL_VERSION):
        _deny()
    attempt1 = db.execute(select(AgentAttempt).where(
        AgentAttempt.id == first.id).with_for_update()).scalar_one_or_none()
    attempt2 = db.execute(select(AgentAttempt).where(
        AgentAttempt.id == second.id).with_for_update()).scalar_one_or_none()
    if any(a is None for a in (attempt1, attempt2)):
        _deny()
    for d, a in ((first, attempt1), (second, attempt2)):
        if (a.conversation_id != conversation_id or a.turn_id != d.turn_id
                or a.run_id != d.turn_id or a.step_id != d.step_id
                or a.status != "succeeded" or a.tool_name != d.tool_name
                or a.tool_version != d.tool_version
                or a.error_code is not None):
            _deny()
    args1, args2 = _obj(first.arguments_json), _obj(second.arguments_json)
    receipt1, receipt2 = _obj(first.receipt_json), _obj(second.receipt_json)
    first_binding = first_step["argument_bindings"]["term"]
    definition = receipt1.get("definition")
    if (args1 != {"term": first_binding.get("value")}
            or type(definition) is not str or not definition
            or receipt1.get("ok") is not True
            or receipt1.get("definition_availability") == "truncated"
            or bounded_projection("mcp_reference_lookup", receipt1) != receipt1
            or args2 != {"text": definition}
            or receipt2.get("ok") is not True
            or bounded_projection("text_stats", receipt2) != receipt2
            or type(receipt2.get("characters")) is not int
            or type(receipt2.get("words")) is not int
            or receipt2["characters"] != len(definition)
            or receipt2["words"] != len(definition.split())):
        _deny()
    for a, args, receipt in ((attempt1, args1, receipt1),
                              (attempt2, args2, receipt2)):
        if (a.arguments_digest != arguments_digest(args)
                or _obj(a.result_projection) != receipt):
            _deny()
    expected = copy.deepcopy(pristine)
    for step, d, args, refs in (
        (first_step, first, args1, []),
        (second_step, second, args2, [{"source_dispatch_id": first.id}]),
    ):
        next(s for s in expected["steps"] if s["id"] == step["id"]
             )["status"] = "completed"
        expected["executions"][str(step["id"])] = {
            "dispatch_id": d.id, "step_id": step["id"],
            "plan_revision": 2, "tool_name": d.tool_name,
            "tool_version": d.tool_version,
            "resolved_arguments_digest": arguments_digest(args),
            "worker_generation": d.worker_generation,
            "worker_token_digest": d.worker_token_digest,
            "source_evidence_refs": refs,
        }
    if evolved != expected:
        _deny()
    intermediate = copy.deepcopy(pristine)
    intermediate["steps"][0]["status"] = "completed"
    intermediate["executions"][str(first_id)] = expected["executions"][str(first_id)]
    intermediate_raw = json.dumps(intermediate, ensure_ascii=False)
    _verified_turn(
        db, cid=conversation_id, tid=first.turn_id,
        raw=anchor.approved_candidate_json, terminal=True,
        route_kind="plan_resume", reason="dependency_revision_resume_prepare_only")
    _verified_turn(
        db, cid=conversation_id, tid=second.turn_id,
        raw=intermediate_raw, terminal=False,
        route_kind="plan_resume",
        reason="dependency_revision_resume_prepare_only",
        expected_request=request_id,
    )

    task = db.execute(select(AgentTaskState).where(
        AgentTaskState.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    expected_states = [{
        "id": s["id"], "status": "completed",
        "evidence_tool": s["expected_tool"],
    } for s in expected["steps"]]
    try:
        states = json.loads(task.step_states) if task is not None else None
    except (ValueError, TypeError):
        _deny()
    if (task is None or task.state != "completed" or task.plan_revision != 2
            or states != expected_states):
        _deny()
    transitions = db.scalars(select(AgentStateTransition).where(
        AgentStateTransition.conversation_id == conversation_id
    ).with_for_update()).all()
    origins = [t for t in transitions if t.reason_code == "dependency_plan_revision_saved"]
    completed = [t for t in transitions if t.reason_code == "verified_step_completed"]
    state_changes = [t for t in transitions if t.reason_code == "task_status_changed"]
    if (len(origins) != 1 or len(completed) != 2
            or len(state_changes) != 2
            or origins[0].task_id != task.task_id
            or origins[0].entity_type != "task"
            or origins[0].to_state != "planned"
            or origins[0].revision + 2 != task.revision):
        _deny()
    for index, (t, d) in enumerate(zip(
        sorted(completed, key=lambda r: r.revision), (first, second)), start=1):
        if (t.task_id != task.task_id
                or t.entity_type != "step"
                or t.step_id != d.step_id or t.attempt_id != d.id
                or t.from_state != "pending" or t.to_state != "completed"
                or t.revision != origins[0].revision + index):
            _deny()
    for index, (t, former, latter) in enumerate(zip(
        sorted(state_changes, key=lambda r: r.revision),
        ("planned", "partial"), ("partial", "completed")), start=1):
        if (t.task_id != task.task_id
                or t.entity_type != "task"
                or t.step_id is not None or t.attempt_id is not None
                or t.from_state != former or t.to_state != latter
                or t.revision != origins[0].revision + index):
            _deny()
    return second


def finalize_second_revised_turn_in_session(
    db, *, settings: Settings, owner_id: str, conversation_id: str,
    turn_id: str, request_id: str, expected_raw: str,
    fence: DependencyFenceToken,
) -> str:
    """Atomic finalization of a two-Turn, two-step physically proven v2 plan."""
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
            or type(fence) is not DependencyFenceToken
            or fence.turn_id != turn_id
            or fence.conversation_id != conversation_id):
        _deny()
    _verify_two_step_chain_in_session(
        db, owner_id=owner_id, conversation_id=conversation_id,
        expected_raw=expected_raw, turn_id=turn_id,
        request_id=request_id, fence=fence)
    claim = db.get(PlanExecutionClaim, conversation_id)
    turn = db.get(Turn, turn_id)
    if claim is None or claim.turn_id != turn_id or turn is None:
        _deny()
    response = "本轮已保存经验证的执行结果，计划步骤已全部完成。"
    if turn.assistant_message_id is not None:
        _deny()
    answer = Message(
        id=str(uuid.uuid4()), conversation_id=conversation_id,
        role="assistant", status="completed", content=response)
    db.add(answer)
    db.flush()
    turn.assistant_message_id = answer.id
    turn.state = "completed"
    turn.failure_code = None
    turn.output_chars = len(response)
    revoke_dependency_fence_in_session(
        db, conversation_id=conversation_id, turn_id=turn_id)
    db.delete(claim)
    db.flush()
    return "completed"
