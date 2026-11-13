"""B3c-4 Slice A: physical proof of exactly ONE v2 checkpointed step.

Read-only attestation. NOT an execution grant, recovery permit, next-Turn
admission, or proof of external exactly-once tool effects. The pristine B3c-1
anchor is the authority for all unexecuted steps and the inherited v1 prefix.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyRevisionAnchor, DependencyV2StepReservation,
    DependencyWorkerFence, PlanExecutionClaim, PlanToolDispatch,
    PrimaryRouteEvidence, Turn, Message,
)
from services.dependency_revision_dispatch import _verify_inherited
from services.dependency_revision_fingerprint import revision_fingerprint
from services.evidence_store import arguments_digest, bounded_projection


@dataclass(frozen=True)
class VerifiedRevisedCheckpoint:
    conversation_id: str
    revision_turn_id: str
    completed_v2_step_id: int
    dispatch_id: str
    snapshot_sha256: str
    remaining_step_ids: tuple[int, ...]


def _deny():
    raise DependencyPlanError("dependency_v2_progress_lineage_untrusted")


def _obj(raw):
    try:
        result = json.loads(raw)
    except (TypeError, ValueError):
        _deny()
    if type(result) is not dict:
        _deny()
    return result


def verify_revised_checkpoint_progress(
    session_factory, *, owner_id: str, conversation_id: str,
    expected_raw: str,
) -> VerifiedRevisedCheckpoint:
    """Verify one post-checkpoint v2 step from durable DB facts.

    Locks Plan → Claim → Fence → Dispatch → Attempt → Task so an in-flight
    writer cannot invalidate the inspected proof before this transaction
    completes. The returned immutable object conveys NO future authority.
    """
    if not all(type(x) is str and x for x in
               (owner_id, conversation_id, expected_raw)):
        _deny()
    plan = _obj(expected_raw)
    with session_factory.begin() as db:
        owner = db.get(Conversation, conversation_id)
        saved = db.execute(select(ConversationPlan).where(
            ConversationPlan.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        claim = db.execute(select(PlanExecutionClaim).where(
            PlanExecutionClaim.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        fence = db.execute(select(DependencyWorkerFence).where(
            DependencyWorkerFence.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        if (owner is None or owner.owner_id != owner_id
                or saved is None or saved.snapshot != expected_raw
                or claim is None or claim.snapshot != expected_raw
                or fence is None or fence.turn_id != claim.turn_id
                or fence.state != "active"):
            _deny()
        turn = db.get(Turn, claim.turn_id)
        host = db.get(PrimaryRouteEvidence, claim.turn_id)
        user = (db.get(Message, turn.user_message_id)
                if turn is not None and turn.user_message_id else None)
        if (turn is None or turn.conversation_id != conversation_id
                or turn.state != "running" or not turn.request_id
                or host is None or host.conversation_id != conversation_id
                or host.route_kind != "plan_resume"
                or host.disposition != "allow"
                or host.schema_version != "primary-route-v1"
                or host.admission_reason != "dependency_revision_resume_prepare_only"
                or user is None or user.conversation_id != conversation_id
                or user.role != "user" or user.status != "completed"
                or host.approved_request_digest is None
                or host.approved_request_digest
                    != hashlib.sha256(user.content.encode("utf-8")).hexdigest()):
            _deny()
        anchor = db.get(DependencyRevisionAnchor, conversation_id)
        if anchor is None:
            _deny()
        pristine = _obj(anchor.approved_candidate_json)
        original_approval = db.get(PrimaryRouteEvidence, anchor.revision_turn_id)
        original_turn = db.get(Turn, anchor.revision_turn_id)
        revision_user = (db.get(Message, original_turn.user_message_id)
                         if original_turn and original_turn.user_message_id else None)
        revision_assistant = (db.get(Message, original_turn.assistant_message_id)
                              if original_turn and original_turn.assistant_message_id else None)
        try:
            refs = json.loads(anchor.origin_refs_json)
        except (TypeError, ValueError):
            _deny()
        if (original_approval is None or original_approval.conversation_id != conversation_id
                or original_approval.route_kind != "plan_revise"
                or original_approval.schema_version != "primary-route-v1"
                or original_approval.disposition != "allow"
                or original_approval.admission_reason != "dependency_revision_candidate"
                or original_approval.approved_revision_digest != anchor.approved_digest
                or original_turn is None or original_turn.conversation_id != conversation_id
                or original_turn.state != "partial" or original_turn.failure_code is not None
                or revision_user is None or revision_user.role != "user"
                or revision_user.status != "completed"
                or revision_assistant is None or revision_assistant.role != "assistant"
                or revision_assistant.status != "partial"
                or revision_fingerprint(pristine) != anchor.approved_digest
                or type(pristine.get("lineage")) is not dict
                or pristine["lineage"].get("origin_refs") != refs
                or pristine["lineage"].get("parent_snapshot_sha256")
                   != anchor.parent_snapshot_sha256
                or pristine.get("revision") != 2
                or pristine.get("plan_executor_version") != "dependency-plan-read-replan-v1"
                or type(pristine.get("steps")) is not list
                or type(pristine.get("executions")) is not dict):
            _deny()

        base_steps = pristine["steps"]
        pending = [s for s in base_steps
                   if type(s) is dict and s.get("status") == "pending"]
        if not pending:
            _deny()
        first = pending[0]
        step_id = first.get("id")
        if type(step_id) is not int or step_id < 1:
            _deny()
        dispatches = db.scalars(select(PlanToolDispatch).where(
            PlanToolDispatch.conversation_id == conversation_id
        ).order_by(PlanToolDispatch.step_id, PlanToolDispatch.id)
            .with_for_update()).all()
        actual_v2 = [d for d in dispatches if d.plan_revision == 2]
        if len(actual_v2) != 1 or actual_v2[0].step_id != step_id:
            _deny()
        item = actual_v2[0]
        original = [d for d in dispatches if d.id != item.id]
        _verify_inherited(db, conversation_id=conversation_id,
                          checkpoint=pristine, dispatches=original)
        reservation = db.get(DependencyV2StepReservation,
                             (conversation_id, step_id))
        reservations = db.scalars(select(DependencyV2StepReservation).where(
            DependencyV2StepReservation.conversation_id == conversation_id)).all()
        attempt = db.execute(select(AgentAttempt).where(
            AgentAttempt.id == item.id
        ).with_for_update()).scalar_one_or_none()
        if (len(reservations) != 1 or reservation is None
                or reservation.dispatch_id != item.id
                or reservation.turn_id != item.turn_id
                or item.turn_id != claim.turn_id
                or item.worker_generation != fence.generation
                or item.worker_token_digest != fence.token_digest
                or item.status != "checkpointed"
                or item.tool_name != first.get("expected_tool")
                or item.tool_version != first.get("tool_version")
                or attempt is None or attempt.id != item.id
                or attempt.conversation_id != conversation_id
                or attempt.turn_id != item.turn_id
                or attempt.step_id != step_id
                or attempt.status != "succeeded"
                or attempt.tool_name != item.tool_name
                or attempt.tool_version != item.tool_version):
            _deny()
        args = _obj(item.arguments_json)
        receipt = _obj(item.receipt_json)
        projection = _obj(attempt.result_projection)
        digest = arguments_digest(args)
        if (receipt.get("ok") is not True
                or bounded_projection(item.tool_name, receipt) != receipt
                or projection != receipt
                or attempt.arguments_digest != digest):
            _deny()
        if item.tool_name == "mcp_reference_lookup":
            binding = first.get("argument_bindings", {}).get("term")
            if (type(binding) is not dict or binding.get("kind") != "literal"
                    or first.get("depends_on") != []
                    or args != {"term": binding.get("value")}
                    or type(receipt.get("definition")) is not str
                    or not receipt["definition"]
                    or receipt.get("definition_availability") == "truncated"):
                _deny()
            refs_of_step = []
        elif item.tool_name == "text_stats":
            binding = first.get("argument_bindings", {}).get("text")
            if type(binding) is not dict:
                _deny()
            source_step_id = binding.get("source_step_id")
            record = pristine["executions"].get(str(source_step_id))
            if (binding.get("kind") != "observation_ref"
                    or binding.get("field") != "definition"
                    or source_step_id not in first.get("depends_on", [])
                    or type(record) is not dict
                    or record.get("plan_revision") != 1):
                _deny()
            source = next((d for d in original
                           if d.id == record.get("dispatch_id")), None)
            if source is None or source.tool_name != "mcp_reference_lookup":
                _deny()
            inherited_receipt = _obj(source.receipt_json)
            definition = inherited_receipt.get("definition")
            if (type(definition) is not str or not definition
                    or args != {"text": definition}
                    or type(receipt.get("characters")) is not int
                    or receipt["characters"] != len(definition)
                    or type(receipt.get("words")) is not int
                    or receipt["words"] != len(definition.split())):
                _deny()
            refs_of_step = [{"source_dispatch_id": source.id}]
        else:
            _deny()

        expected = copy.deepcopy(pristine)
        changed_step = next(s for s in expected["steps"] if s["id"] == step_id)
        changed_step["status"] = "completed"
        expected["executions"][str(step_id)] = {
            "step_id": step_id,
            "dispatch_id": item.id,
            "plan_revision": 2,
            "tool_name": item.tool_name,
            "tool_version": item.tool_version,
            "resolved_arguments_digest": digest,
            "source_evidence_refs": refs_of_step,
        }
        if plan != expected or set(plan) != set(pristine):
            _deny()
        task = db.execute(select(AgentTaskState).where(
            AgentTaskState.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        if task is None or task.plan_revision != 2:
            _deny()
        projection_expected = [{
            "id": s["id"], "status": s["status"],
            "evidence_tool": s["expected_tool"] if s["status"] == "completed"
                             else None,
        } for s in expected["steps"]]
        try:
            task_steps = json.loads(task.step_states)
        except (ValueError, TypeError):
            _deny()
        state = "completed" if all(s["status"] == "completed"
                                   for s in expected["steps"]) else "partial"
        if task_steps != projection_expected or task.state != state:
            _deny()
        origin = db.scalars(select(AgentStateTransition).where(
            AgentStateTransition.conversation_id == conversation_id,
            AgentStateTransition.reason_code == "dependency_plan_revision_saved")).all()
        verified = db.scalars(select(AgentStateTransition).where(
            AgentStateTransition.conversation_id == conversation_id,
            AgentStateTransition.reason_code == "verified_step_completed",
            AgentStateTransition.step_id == step_id)).all()
        if (len(origin) != 1 or len(verified) != 1
                or origin[0].entity_type != "task"
                or origin[0].step_id is not None or origin[0].attempt_id is not None
                or origin[0].to_state not in ("planned", "partial")
                or origin[0].revision + 1 != task.revision
                or origin[0].task_id != task.task_id
                or verified[0].entity_type != "step"
                or verified[0].task_id != task.task_id
                or verified[0].revision != task.revision
                or verified[0].attempt_id != item.id
                or verified[0].from_state != "pending"
                or verified[0].to_state != "completed"):
            _deny()
        return VerifiedRevisedCheckpoint(
            conversation_id=conversation_id,
            revision_turn_id=anchor.revision_turn_id,
            completed_v2_step_id=step_id,
            dispatch_id=item.id,
            snapshot_sha256=hashlib.sha256(expected_raw.encode("utf-8")).hexdigest(),
            remaining_step_ids=tuple(s["id"] for s in expected["steps"]
                                     if s["status"] == "pending"),
        )
