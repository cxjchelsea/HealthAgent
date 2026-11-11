"""S25-07 Gate A: finalize a *verified* read failure without replay or plan advance.

Only an already-authorized S25-06 runner calls this function, after the
existing durable journal committed an observed_failure Receipt. It is not
a model-callable tool, independent execution permit, or recovery operator.
"""
import hashlib
import json
import uuid

from sqlalchemy import select, update

from agent.dependency_plan_host import (
    PreparedDependencyRead, _step_for_read, _verify_completed_records,
)
from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, PlanExecutionClaim, PlanToolDispatch, PrimaryRouteEvidence,
    Turn, now,
)
from services.dependency_fencing import (
    DependencyFenceToken, assert_dependency_fence_in_session,
)
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_route_evidence import snapshot_digest


def _deny(code: str):
    raise DependencyPlanError(code)


def finalize_dependency_failure_in_session(
    db, *, binding: PreparedDependencyRead, initial_turn_raw: str,
    expected_raw: str, dispatch_id: str,
    fence: DependencyFenceToken | None,
) -> str:
    """Atomically bind a negative Receipt to a failed Attempt and task evidence.

    This function deliberately does NOT advance the plan, release its Claim,
    dispatch again, make the step completed, or assert the failure retryable.
    Failure-finalized remains permanently protected by the duplicate-step
    guard until a later, separately designed replan/attempt protocol.
    """
    if (type(binding) is not PreparedDependencyRead
            or not all(type(x) is str and x for x in
                       (initial_turn_raw, expected_raw, dispatch_id))
            or hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
               != binding.checkpoint_digest
            or fence is None):
        _deny("dependency_failure_binding_invalid")
    # Match the established Plan -> Claim -> Dispatch -> Fence lock order.
    plan_row = db.execute(select(ConversationPlan).where(
        ConversationPlan.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    claim = db.execute(select(PlanExecutionClaim).where(
        PlanExecutionClaim.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    owner = db.get(Conversation, binding.conversation_id)
    turn = db.get(Turn, binding.turn_id)
    primary = db.get(PrimaryRouteEvidence, binding.turn_id)
    expected_route = ("plan_execute_first" if binding.control == "execute_first"
                      else "plan_resume" if binding.control == "resume" else None)
    if (plan_row is None or plan_row.snapshot != expected_raw
            or claim is None or claim.turn_id != binding.turn_id
            or claim.snapshot != expected_raw
            or owner is None or owner.owner_id != binding.owner_id
            or turn is None or turn.conversation_id != binding.conversation_id
            or turn.request_id != binding.request_id or turn.state != "running"
            or primary is None or primary.conversation_id != binding.conversation_id
            or primary.schema_version != "primary-route-v1"
            or primary.route_kind != expected_route
            or primary.disposition != "allow"
            or primary.admission_reason != "primary_v1_plan_execution_candidate"
            or primary.preturn_snapshot_digest != snapshot_digest(initial_turn_raw)):
        _deny("dependency_failure_claim_conflict")
    try:
        checkpoint = json.loads(expected_raw)
    except (ValueError, TypeError):
        _deny("dependency_invalid_schema")
    if (type(checkpoint) is not dict
            or checkpoint.get("plan_schema_version") != "dependency-plan-v1"
            or checkpoint.get("plan_executor_version") != "dependency-plan-read-v1"
            or checkpoint.get("revision") != binding.plan_revision):
        _deny("dependency_version_mismatch")
    step = _step_for_read(checkpoint, binding.step_id)
    _verify_completed_records(
        db, checkpoint=checkpoint, conversation_id=binding.conversation_id)
    if (step.get("expected_tool") != binding.tool_name
            or step.get("tool_version") != binding.tool_version
            or str(binding.step_id) in checkpoint["executions"]):
        _deny("dependency_failure_binding_invalid")
    dispatch = db.execute(select(PlanToolDispatch).where(
        PlanToolDispatch.id == dispatch_id
    ).with_for_update()).scalar_one_or_none()
    if (dispatch is None or dispatch.conversation_id != binding.conversation_id
            or dispatch.turn_id != binding.turn_id
            or dispatch.step_id != binding.step_id
            or dispatch.tool_name != binding.tool_name
            or dispatch.tool_version != binding.tool_version
            or dispatch.status != "observed_failure"):
        _deny("dependency_failure_receipt_conflict")
    assert_dependency_fence_in_session(
        db, conversation_id=binding.conversation_id,
        turn_id=binding.turn_id, fence=fence)
    if (dispatch.worker_generation != fence.generation
            or dispatch.worker_token_digest
               != hashlib.sha256(fence.secret.encode("utf-8")).hexdigest()):
        _deny("dependency_worker_fenced")
    try:
        actual = json.loads(dispatch.arguments_json)
        receipt = json.loads(dispatch.receipt_json)
    except (ValueError, TypeError):
        _deny("dependency_failure_receipt_conflict")
    if (type(actual) is not dict or actual != binding.arguments
            or arguments_digest(actual) != binding.arguments_digest
            or type(receipt) is not dict
            or set(receipt) != {"ok", "error"}
            or receipt["ok"] is not False
            or type(receipt["error"]) is not str
            or len(receipt["error"]) > 80
            or bounded_projection(binding.tool_name, receipt) != receipt
            or db.get(AgentAttempt, dispatch.id) is not None):
        _deny("dependency_failure_receipt_conflict")

    # Validate and advance the existing S25-03 projection without changing
    # the pending/completed flags or manufacturing a completed execution.
    projection = [
        {"id": s["id"], "status": s["status"],
         "evidence_tool": s["expected_tool"] if s["status"] == "completed"
         else None}
        for s in checkpoint["steps"]
    ]
    encoded = json.dumps(projection, ensure_ascii=False)
    task = db.execute(select(AgentTaskState).where(
        AgentTaskState.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    if task is None:
        task_id, revision, prior_state = str(uuid.uuid4()), 1, None
        db.add(AgentTaskState(
            conversation_id=binding.conversation_id, task_id=task_id,
            revision=revision, plan_revision=binding.plan_revision,
            state="blocked", step_states=encoded, updated_at=now()))
    else:
        if (task.plan_revision != binding.plan_revision
                or task.step_states != encoded
                or task.state not in {"partial", "blocked"}):
            _deny("dependency_task_projection_conflict")
        task_id, revision, prior_state = task.task_id, task.revision + 1, task.state
        changed = db.execute(update(AgentTaskState).where(
            AgentTaskState.conversation_id == binding.conversation_id,
            AgentTaskState.revision == task.revision
        ).values(revision=revision, state="blocked", updated_at=now()))
        if changed.rowcount != 1:
            _deny("dependency_task_projection_conflict")
    attempt = AgentAttempt(
        id=dispatch.id, conversation_id=binding.conversation_id,
        turn_id=binding.turn_id, run_id=binding.turn_id, step_id=binding.step_id,
        tool_name=binding.tool_name, tool_version=binding.tool_version,
        arguments_digest=binding.arguments_digest, status="failed",
        error_code="dependency_tool_reported_failure",
        result_projection=json.dumps(receipt, ensure_ascii=False),
    )
    db.add(attempt)
    if prior_state != "blocked":
        db.add(AgentStateTransition(
            id=str(uuid.uuid4()), conversation_id=binding.conversation_id,
            task_id=task_id, revision=revision, entity_type="task",
            step_id=None, from_state=prior_state, to_state="blocked",
            reason_code="verified_tool_failure", created_at=now()))
    db.add(AgentStateTransition(
        id=str(uuid.uuid4()), conversation_id=binding.conversation_id,
        task_id=task_id, revision=revision, entity_type="step",
        step_id=binding.step_id, from_state="pending", to_state="pending",
        reason_code="verified_step_failed", attempt_id=dispatch.id,
        created_at=now()))
    dispatch.status = "failure_finalized"
    db.flush()
    return "dependency_tool_reported_failure"
