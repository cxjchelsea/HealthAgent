"""S25-07 Gate B2a: authenticated, fail-closed revision CAS.

NOT an API, tool or execution permit. Primary route plan_revise for dependency
snapshots is still denied by the production Host; only B2b may wire it after a
separate admission review. This transaction makes no tool invocations.
"""
import hashlib
import json
import uuid

from sqlalchemy import select, update
from agent.dependency_plan_host import _verify_completed_records
from agent.dependency_plan_schema import DependencyPlanError
from agent.dependency_revision_candidate import (
    RevisionCandidate, compile_revision_candidate,
)
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyWorkerFence, Message, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, now,
)
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_route_evidence import snapshot_digest


def _deny(code: str):
    raise DependencyPlanError(code)


def commit_dependency_revision_in_session(
    db, *, owner_id: str, conversation_id: str, turn_id: str,
    request_id: str, user_message_id: str, expected_raw: str,
    proposal: RevisionCandidate,
) -> dict:
    """Atomically save an independently recompiled revision candidate.

    The per-Turn PrimaryRouteEvidence is a prerequisite, not a permit that
    callers may mint here. No active Claim, active Fence or dispatched pending
    step is tolerated. A model-supplied lineage is never trusted.
    """
    if (not all(type(x) is str and x.strip() for x in (
            owner_id, conversation_id, turn_id, request_id,
            user_message_id, expected_raw))
            or type(proposal) is not RevisionCandidate):
        _deny("dependency_revision_invalid_input")
    # Plan -> Claim -> Fence -> Dispatch -> TaskState lock hierarchy.
    plan = db.execute(select(ConversationPlan).where(
        ConversationPlan.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    claim = db.execute(select(PlanExecutionClaim).where(
        PlanExecutionClaim.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    fence = db.execute(select(DependencyWorkerFence).where(
        DependencyWorkerFence.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    if plan is None or plan.snapshot != expected_raw:
        _deny("dependency_revision_snapshot_conflict")
    if claim is not None or (fence is not None and fence.state != "fenced"):
        _deny("dependency_revision_execution_active")
    owner = db.get(Conversation, conversation_id)
    turn = db.get(Turn, turn_id)
    message = db.get(Message, user_message_id)
    admission = db.get(PrimaryRouteEvidence, turn_id)
    if (owner is None or owner.owner_id != owner_id
            or turn is None or turn.conversation_id != conversation_id
            or turn.request_id != request_id or turn.state != "running"
            or message is None or message.conversation_id != conversation_id
            or message.role != "user" or not message.content.strip()
            or admission is None or admission.conversation_id != conversation_id
            or admission.schema_version != "primary-route-v1"
            or admission.route_kind != "plan_revise"
            or admission.disposition != "allow"
            or admission.admission_reason != "dependency_revision_candidate"
            or admission.preturn_snapshot_digest != snapshot_digest(expected_raw)):
        _deny("dependency_revision_host_denied")
    raw_sha = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
    if proposal.expected_raw_sha256 != raw_sha or proposal.parent_revision != 1:
        _deny("dependency_revision_snapshot_conflict")
    try:
        parent = json.loads(expected_raw)
    except (TypeError, ValueError):
        _deny("dependency_invalid_schema")
    if type(parent) is not dict or type(proposal.candidate) is not dict:
        _deny("dependency_revision_invalid_input")
    proposed_steps = proposal.candidate.get("steps")
    if type(proposed_steps) is not list:
        _deny("dependency_revision_invalid_input")
    # Recompile from this exact, locked parent using text stored in the DB.
    # All model-provided revision, lineage and completed records are discarded.
    pending = [s for s in proposed_steps if type(s) is dict
               and s.get("status") == "pending"]
    verified = compile_revision_candidate(
        expected_raw=expected_raw, replacement_pending=pending,
        current_user_message=message.content)
    if proposal.candidate != verified.candidate:
        _deny("dependency_revision_candidate_tampered")
    old_steps = parent["steps"]
    completed = [s for s in old_steps if s["status"] == "completed"]
    completed_ids = {s["id"] for s in completed}
    dispatches = db.scalars(select(PlanToolDispatch).where(
        PlanToolDispatch.conversation_id == conversation_id
    ).order_by(PlanToolDispatch.step_id, PlanToolDispatch.id)
        .with_for_update()).all()
    # No partial, unknown, failed or succeeded dispatch attached to a pending
    # step may be superseded here. Gate C handles retry with new attempt model.
    if any(d.step_id not in completed_ids for d in dispatches):
        _deny("dependency_revision_pending_dispatch_exists")
    if len(dispatches) != len(completed_ids):
        _deny("dependency_revision_dispatch_lineage_conflict")
    for d in dispatches:
        record = parent["executions"].get(str(d.step_id))
        if record is None or record.get("dispatch_id") != d.id:
            _deny("dependency_revision_dispatch_lineage_conflict")
        attempt = db.execute(select(AgentAttempt).where(
            AgentAttempt.id == d.id
        ).with_for_update()).scalar_one_or_none()
        try:
            args = json.loads(d.arguments_json)
            receipt = json.loads(d.receipt_json)
            projection = json.loads(attempt.result_projection) if attempt else None
        except (TypeError, ValueError):
            _deny("dependency_revision_lineage_untrusted")
        if (d.status != "checkpointed" or type(args) is not dict
                or type(receipt) is not dict or receipt.get("ok") is not True
                or attempt is None or attempt.status != "succeeded"
                or attempt.conversation_id != conversation_id
                or attempt.turn_id != d.turn_id
                or attempt.step_id != d.step_id
                or attempt.tool_name != d.tool_name
                or attempt.tool_version != d.tool_version
                or attempt.arguments_digest != arguments_digest(args)
                or record["resolved_arguments_digest"] != arguments_digest(args)
                or projection != bounded_projection(d.tool_name, receipt)):
            _deny("dependency_revision_lineage_untrusted")
    _verify_completed_records(
        db, checkpoint=parent, conversation_id=conversation_id)
    # S25-06's text_stats dispatcher only accepts observed text from an
    # authenticated predecessor. Even a syntactically valid new literal must
    # not be persisted as future runnable under B2a.
    for s in verified.candidate["steps"]:
        if (s["status"] == "pending" and s["expected_tool"] == "text_stats"
                and s["argument_bindings"]["text"]["kind"] != "observation_ref"):
            _deny("dependency_revision_binding_not_executable")
    changed_plan = db.execute(update(ConversationPlan).where(
        ConversationPlan.conversation_id == conversation_id,
        ConversationPlan.snapshot == expected_raw,
    ).values(snapshot=json.dumps(verified.candidate, ensure_ascii=False)))
    if changed_plan.rowcount != 1:
        _deny("dependency_revision_snapshot_conflict")
    task = db.execute(select(AgentTaskState).where(
        AgentTaskState.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    old_projection = [
        {"id": s["id"], "status": s["status"],
         "evidence_tool": s["expected_tool"] if s["status"] == "completed" else None}
        for s in old_steps
    ]
    old_projection_raw = json.dumps(old_projection, ensure_ascii=False)
    new_state = "partial" if completed else "planned"
    if task is not None:
        if (task.plan_revision != 1
                or task.step_states != old_projection_raw
                or task.state not in {"partial", "planned", "blocked"}):
            _deny("dependency_revision_task_conflict")
        task_id, revision, prior_state = task.task_id, task.revision + 1, task.state
        changed = db.execute(update(AgentTaskState).where(
            AgentTaskState.conversation_id == conversation_id,
            AgentTaskState.revision == task.revision
        ).values(revision=revision, plan_revision=2,
                 state=new_state, updated_at=now()))
        if changed.rowcount != 1:
            _deny("dependency_revision_task_conflict")
    else:
        task_id, revision, prior_state = str(uuid.uuid4()), 1, None
        db.add(AgentTaskState(
            conversation_id=conversation_id, task_id=task_id,
            revision=revision, plan_revision=2, state=new_state,
            step_states=old_projection_raw, updated_at=now()))
    db.add(AgentStateTransition(
        id=str(uuid.uuid4()), conversation_id=conversation_id,
        task_id=task_id, revision=revision, entity_type="task",
        step_id=None, from_state=prior_state, to_state=new_state,
        reason_code="dependency_plan_revision_saved", created_at=now()))
    db.flush()
    return verified.candidate
