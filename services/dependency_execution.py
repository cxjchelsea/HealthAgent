"""S25-06 Slice D: durable, fail-closed *step checkpoint* transaction.

This is NOT an HTTP route or a tool dispatch permit. It consumes a real M4
observed Dispatch produced by the trusted Host and atomically advances its
plan, Claim, AgentAttempt, and S25-03 task-state evidence.
"""
import copy
import hashlib
import json
import uuid

from sqlalchemy import select, update

from agent.dependency_plan_host import PreparedDependencyRead
from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, ConversationPlan,
    PlanExecutionClaim, PlanToolDispatch, Turn, now,
)
from services.evidence_store import arguments_digest, bounded_projection


def _deny(code):
    raise DependencyPlanError(code)


def _raw_digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strict_receipt(receipt_json):
    try:
        value = json.loads(receipt_json)
    except (TypeError, ValueError):
        _deny("dependency_untrusted_observation")
    if type(value) is not dict or value.get("ok") is not True:
        _deny("dependency_untrusted_observation")
    return value


def _project_step_in_session(db, *, binding, checkpoint, dispatch, receipt):
    """One verified incremental projection. Legacy finalizer stays unchanged."""
    if db.get(AgentAttempt, dispatch.id) is not None:
        _deny("dependency_execution_duplicate_attempt")
    attempt = AgentAttempt(
        id=dispatch.id, conversation_id=binding.conversation_id,
        turn_id=binding.turn_id, run_id=binding.turn_id,
        step_id=binding.step_id, tool_name=binding.tool_name,
        tool_version=binding.tool_version,
        arguments_digest=binding.arguments_digest, status="succeeded",
        result_projection=json.dumps(
            bounded_projection(binding.tool_name, receipt),
            ensure_ascii=False),
    )
    db.add(attempt)
    db.flush()
    old = db.execute(select(AgentTaskState).where(
        AgentTaskState.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    data = json.dumps(
        [{"id": s["id"], "status": s["status"],
          "evidence_tool": s["expected_tool"] if s["status"] == "completed" else None}
         for s in checkpoint["steps"]], ensure_ascii=False)
    state = ("completed" if all(s["status"] == "completed" for s in checkpoint["steps"])
             else "partial")
    if old is None:
        task_id, revision = str(uuid.uuid4()), 1
        db.add(AgentTaskState(
            conversation_id=binding.conversation_id, task_id=task_id,
            revision=revision, plan_revision=checkpoint["revision"],
            state=state, step_states=data, updated_at=now()))
        old_step_state = None
        prior_task_state = None
    else:
        task_id, revision = old.task_id, old.revision + 1
        prior_task_state = old.state
        try:
            previous = json.loads(old.step_states)
        except (TypeError, ValueError):
            _deny("dependency_task_projection_conflict")
        if type(previous) is not list:
            _deny("dependency_task_projection_conflict")
        matching = [x for x in previous if type(x) is dict and x.get("id") == binding.step_id]
        old_step_state = matching[0].get("status") if len(matching) == 1 else None
        # A pre-existing projection may not contradict the durable plan.
        if old_step_state not in (None, "pending"):
            _deny("dependency_task_projection_conflict")
        result = db.execute(update(AgentTaskState).where(
            AgentTaskState.conversation_id == binding.conversation_id,
            AgentTaskState.revision == old.revision
        ).values(revision=revision, plan_revision=checkpoint["revision"],
                 state=state, step_states=data, updated_at=now()))
        if result.rowcount != 1:
            _deny("dependency_task_projection_conflict")
    if prior_task_state != state:
        db.add(AgentStateTransition(
            id=str(uuid.uuid4()), conversation_id=binding.conversation_id,
            task_id=task_id, revision=revision,
            entity_type="task", step_id=None, from_state=prior_task_state,
            to_state=state, reason_code="task_status_changed", created_at=now()))
    db.add(AgentStateTransition(
        id=str(uuid.uuid4()), conversation_id=binding.conversation_id,
        task_id=task_id, revision=revision,
        entity_type="step", step_id=binding.step_id,
        from_state=old_step_state, to_state="completed",
        reason_code="verified_step_completed",
        attempt_id=dispatch.id, created_at=now()))


def checkpoint_dependency_step_in_session(
    db, *, binding: PreparedDependencyRead, expected_raw: str, dispatch_id: str,
) -> dict:
    """Atomic commit after M4 has already recorded a successful observation.

    This function is the ONLY proposed new-format step finalizer in Slice D.
    The caller MUST own the existing Host admission and M4 dispatch authority;
    successful preparation alone cannot create a Dispatch.
    """
    if (type(binding) is not PreparedDependencyRead
            or type(expected_raw) is not str
            or type(dispatch_id) is not str or not dispatch_id
            or _raw_digest(expected_raw) != binding.checkpoint_digest):
        _deny("dependency_execution_binding_invalid")

    plan_row = db.execute(select(ConversationPlan).where(
        ConversationPlan.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    claim = db.execute(select(PlanExecutionClaim).where(
        PlanExecutionClaim.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    turn = db.get(Turn, binding.turn_id)
    if (plan_row is None or plan_row.snapshot != expected_raw
            or claim is None or claim.turn_id != binding.turn_id
            or claim.snapshot != expected_raw
            or turn is None or turn.conversation_id != binding.conversation_id
            or turn.request_id != binding.request_id or turn.state != "running"):
        _deny("dependency_execution_claim_conflict")
    try:
        checkpoint = json.loads(expected_raw)
    except (TypeError, ValueError):
        _deny("dependency_invalid_schema")
    if (type(checkpoint) is not dict
            or checkpoint.get("plan_schema_version") != "dependency-plan-v1"
            or checkpoint.get("plan_executor_version") != "dependency-plan-read-v1"
            or type(checkpoint.get("revision")) is not int
            or checkpoint["revision"] != binding.plan_revision
            or type(checkpoint.get("steps")) is not list
            or type(checkpoint.get("executions")) is not dict):
        _deny("dependency_version_mismatch")
    steps = checkpoint["steps"]
    matches = [s for s in steps if type(s) is dict and s.get("id") == binding.step_id]
    pending = [s for s in steps if type(s) is dict and s.get("status") == "pending"]
    if (len(matches) != 1 or not pending or matches[0] is not pending[0]
            or matches[0].get("status") != "pending"
            or matches[0].get("expected_tool") != binding.tool_name
            or matches[0].get("tool_version") != binding.tool_version
            or str(binding.step_id) in checkpoint["executions"]):
        _deny("dependency_execution_binding_invalid")
    dispatch = db.execute(select(PlanToolDispatch).where(
        PlanToolDispatch.id == dispatch_id
    ).with_for_update()).scalar_one_or_none()
    if (dispatch is None or dispatch.conversation_id != binding.conversation_id
            or dispatch.turn_id != binding.turn_id
            or dispatch.step_id != binding.step_id
            or dispatch.tool_name != binding.tool_name
            or dispatch.tool_version != binding.tool_version
            or dispatch.status != "observed"):
        _deny("dependency_execution_receipt_conflict")
    try:
        actual = json.loads(dispatch.arguments_json)
    except (ValueError, TypeError):
        _deny("dependency_execution_receipt_conflict")
    if (type(actual) is not dict or actual != binding.arguments
            or arguments_digest(actual) != binding.arguments_digest):
        _deny("dependency_execution_receipt_conflict")
    receipt = _strict_receipt(dispatch.receipt_json)
    definition_field = bounded_projection(binding.tool_name, receipt)
    if binding.tool_name == "mcp_reference_lookup":
        binding_template = matches[0].get("argument_bindings")
        term = (binding_template.get("term")
                if type(binding_template) is dict else None)
        if (binding.source_dispatch_id is not None
                or matches[0].get("depends_on") != []
                or type(term) is not dict or term.get("kind") != "literal"
                or actual != {"term": term.get("value")}
                or type(receipt.get("definition")) is not str
                or not receipt["definition"]
                or definition_field.get("definition") != receipt["definition"]
                or receipt.get("definition_availability") == "truncated"):
            _deny("dependency_untrusted_observation")
    elif binding.tool_name == "text_stats":
        template = matches[0].get("argument_bindings")
        text_binding = (template.get("text") if type(template) is dict else None)
        if (type(receipt.get("characters")) is not int
                or type(receipt.get("words")) is not int
                or type(text_binding) is not dict
                or text_binding.get("kind") != "observation_ref"
                or text_binding.get("field") != "definition"
                or binding.source_dispatch_id is None):
            _deny("dependency_untrusted_observation")
        source_id = text_binding.get("source_step_id")
        if (type(source_id) is not int
                or source_id not in matches[0].get("depends_on", [])):
            _deny("dependency_untrusted_observation")
        source_record = checkpoint["executions"].get(str(source_id))
        source_step = next(
            (step for step in steps if step.get("id") == source_id), None)
        if (type(source_record) is not dict
                or source_record.get("dispatch_id") != binding.source_dispatch_id
                or type(source_step) is not dict
                or source_step.get("status") != "completed"):
            _deny("dependency_untrusted_observation")
        source = db.get(PlanToolDispatch, binding.source_dispatch_id)
        source_attempt = db.get(AgentAttempt, binding.source_dispatch_id)
        if (source is None or source.conversation_id != binding.conversation_id
                or source.step_id != source_id
                or source.status != "checkpointed"
                or source.tool_name != "mcp_reference_lookup"
                or source.tool_version != source_step.get("tool_version")
                or source_attempt is None
                or source_attempt.status != "succeeded"
                or source_attempt.conversation_id != binding.conversation_id
                or source_attempt.step_id != source_id):
            _deny("dependency_untrusted_observation")
        source_receipt = _strict_receipt(source.receipt_json)
        if (type(source_receipt.get("definition")) is not str
                or source_receipt.get("definition") != actual.get("text")
                or receipt["characters"] != len(actual["text"])
                or receipt["words"] != len(actual["text"].split())):
            _deny("dependency_untrusted_observation")
    else:
        _deny("dependency_host_tool_denied")
    new_checkpoint = copy.deepcopy(checkpoint)
    dest = next(s for s in new_checkpoint["steps"] if s["id"] == binding.step_id)
    dest["status"] = "completed"
    new_checkpoint["executions"][str(binding.step_id)] = {
        "dispatch_id": dispatch.id, "step_id": binding.step_id,
        "plan_revision": binding.plan_revision,
        "tool_name": binding.tool_name, "tool_version": binding.tool_version,
        "resolved_arguments_digest": binding.arguments_digest,
        "source_evidence_refs": (
            [{"source_dispatch_id": binding.source_dispatch_id}]
            if binding.source_dispatch_id else []),
    }
    # Step projection is in the SAME transaction as CAS/Claim/Receipt.
    _project_step_in_session(
        db, binding=binding, checkpoint=new_checkpoint,
        dispatch=dispatch, receipt=receipt)
    replacement = json.dumps(new_checkpoint, ensure_ascii=False)
    changed = db.execute(update(ConversationPlan).where(
        ConversationPlan.conversation_id == binding.conversation_id,
        ConversationPlan.snapshot == expected_raw
    ).values(snapshot=replacement))
    if changed.rowcount != 1:
        _deny("dependency_execution_claim_conflict")
    claim.snapshot = replacement
    dispatch.status = "checkpointed"
    db.flush()
    return new_checkpoint
