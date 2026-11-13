"""Gate B3c-2: internal-only, single-step revision-2 Dispatch intent.

This is NOT a runner, HTTP route, tool call, receipt writer, or checkpoint.
Call inside a caller-owned transaction; commit the transaction before any
future B3c-3 invocation. An unknown dispatch is never automatically replayed.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import ValidationError
from sqlalchemy import select

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError, PINNED_TOOL_VERSION
from agent.dependency_revision_host import PreparedRevisedRead
from agent.dependency_revision_candidate import REVISED_EXECUTOR_VERSION
from agent.tool_registry import READ_REGISTRY, TextStatsArgs
from agent.tools import ReferenceArgs
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, Conversation,
    ConversationPlan, DependencyRevisionAnchor, DependencyV2StepReservation,
    Message, PlanExecutionClaim, PlanToolDispatch, PrimaryRouteEvidence, Turn,
    now,
)
from services.dependency_fencing import (
    DependencyFenceToken, assert_dependency_fence_in_session,
)
from services.dependency_revision_fingerprint import revision_fingerprint
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_route_evidence import snapshot_digest


def _deny(reason: str) -> None:
    raise DependencyPlanError(reason)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decode(value):
    try:
        result = json.loads(value)
    except (ValueError, TypeError):
        _deny("dependency_revision_dispatch_lineage_untrusted")
    if type(result) is not dict:
        _deny("dependency_revision_dispatch_lineage_untrusted")
    return result


def _verify_inherited(db, *, conversation_id, checkpoint, dispatches):
    """Physical, DB-locked revision-1 Dispatch/Attempt/Receipt verification.

    Do not use the model's completed records as proof. The caller holds the
    conversation Plan and Claim locks; rows and all Attempts are locked here.
    """
    steps = checkpoint["steps"]
    lineage = checkpoint["lineage"]
    executions = checkpoint["executions"]
    prefix = [s for s in steps if s["status"] == "completed"]
    prefix_ids = [s["id"] for s in prefix]
    if (lineage.get("preserved_step_ids") != prefix_ids
            or len(lineage.get("origin_refs", [])) != len(prefix)
            or len(dispatches) != len(prefix)
            or {d.step_id for d in dispatches} != set(prefix_ids)
            or set(executions) != {str(i) for i in prefix_ids}):
        _deny("dependency_revision_dispatch_lineage_untrusted")
    by_id = {d.id: d for d in dispatches}
    if len(by_id) != len(dispatches):
        _deny("dependency_revision_dispatch_lineage_untrusted")
    for step, origin in zip(prefix, lineage["origin_refs"]):
        rec = executions.get(str(step["id"]))
        if (type(rec) is not dict or type(origin) is not dict
                or rec.get("step_id") != step["id"]
                or rec.get("plan_revision") != 1
                or rec.get("tool_name") != step["expected_tool"]
                or rec.get("tool_version") != step["tool_version"]
                or origin != {
                    "step_id": step["id"], "dispatch_id": rec.get("dispatch_id"),
                    "plan_revision": 1, "tool_name": rec["tool_name"],
                    "tool_version": rec["tool_version"],
                    "arguments_digest": rec.get("resolved_arguments_digest"),
                }):
            _deny("dependency_revision_dispatch_lineage_untrusted")
        d = by_id.get(rec["dispatch_id"])
        attempt = (db.execute(select(AgentAttempt).where(
            AgentAttempt.id == rec["dispatch_id"]
        ).with_for_update()).scalar_one_or_none())
        if (d is None or d.conversation_id != conversation_id
                or d.step_id != step["id"] or d.status != "checkpointed"
                or d.tool_name != step["expected_tool"]
                or d.tool_version != step["tool_version"]
                or d.plan_revision not in (None, 1)
                or attempt is None or attempt.conversation_id != conversation_id
                or attempt.turn_id != d.turn_id or attempt.step_id != d.step_id
                or attempt.tool_name != d.tool_name
                or attempt.tool_version != d.tool_version
                or attempt.status != "succeeded"):
            _deny("dependency_revision_dispatch_lineage_untrusted")
        source_turn = db.get(Turn, d.turn_id)
        source_host = db.get(PrimaryRouteEvidence, d.turn_id)
        if (source_turn is None or source_turn.conversation_id != conversation_id
                or source_turn.state not in ("partial", "completed")
                or source_host is None or source_host.conversation_id != conversation_id
                or source_host.schema_version != "primary-route-v1"
                or source_host.disposition != "allow"
                or source_host.route_kind not in ("plan_execute_first", "plan_resume")
                or source_host.admission_reason != "primary_v1_plan_execution_candidate"):
            _deny("dependency_revision_dispatch_lineage_untrusted")
        args = _decode(d.arguments_json)
        receipt = _decode(d.receipt_json)
        projection = _decode(attempt.result_projection)
        if (receipt.get("ok") is not True
                or arguments_digest(args) != rec.get("resolved_arguments_digest")
                or attempt.arguments_digest != rec["resolved_arguments_digest"]
                or projection != bounded_projection(d.tool_name, receipt)):
            _deny("dependency_revision_dispatch_lineage_untrusted")
        if d.tool_name == "mcp_reference_lookup":
            term = step["argument_bindings"]["term"]
            if (term.get("kind") != "literal"
                    or args != {"term": term.get("value")}
                    or rec.get("source_evidence_refs") != []
                    or type(receipt.get("definition")) is not str
                    or not receipt["definition"]
                    or receipt.get("definition_availability") == "truncated"
                    or bounded_projection(d.tool_name, receipt).get("definition")
                        != receipt["definition"]):
                _deny("dependency_revision_dispatch_lineage_untrusted")
        elif d.tool_name == "text_stats":
            ref = step["argument_bindings"]["text"]
            prior = executions.get(str(ref.get("source_step_id")))
            source = by_id.get(prior.get("dispatch_id")) if type(prior) is dict else None
            if (ref.get("kind") != "observation_ref"
                    or ref.get("field") != "definition"
                    or source is None or source.step_id >= d.step_id
                    or rec.get("source_evidence_refs")
                        != [{"source_dispatch_id": source.id}]):
                _deny("dependency_revision_dispatch_lineage_untrusted")
            source_receipt = _decode(source.receipt_json)
            definition = source_receipt.get("definition")
            if (type(definition) is not str or not definition
                    or args != {"text": definition}
                    or type(receipt.get("characters")) is not int
                    or receipt["characters"] != len(definition)
                    or type(receipt.get("words")) is not int
                    or receipt["words"] != len(definition.split())):
                _deny("dependency_revision_dispatch_lineage_untrusted")
        else:
            _deny("dependency_revision_dispatch_lineage_untrusted")


def begin_revised_dependency_dispatch_in_session(
    db, *, settings: Settings, binding: PreparedRevisedRead,
    expected_raw: str, initial_turn_raw: str,
    fence: DependencyFenceToken | None,
) -> str:
    """Insert exactly one v2 dispatch_unknown + durable step reservation.

    The caller MUST commit before any later invocation; B3c-2 never invokes.
    A nonconforming/replayed row fails closed; the DB unique reservation also
    rejects racing independent workers in PostgreSQL.
    """
    if (type(settings) is not Settings
            or not settings.primary_route_v1_active
            or not settings.s25_06_dependency_plan_enabled
            or not settings.s25_07_revision_resume_prepare_enabled
            or not settings.s25_07_revision_dispatch_intent_enabled
            or settings.route_admission_mode != "guarded"
            or settings.understanding_strategy != "structured"
            or type(binding) is not PreparedRevisedRead
            or type(expected_raw) is not str
            or not expected_raw or initial_turn_raw != expected_raw
            or binding.plan_revision != 2
            or binding.checkpoint_digest != _sha(expected_raw)
            or arguments_digest(binding.arguments) != binding.arguments_digest
            or type(fence) is not DependencyFenceToken):
        _deny("dependency_revision_dispatch_not_authorized")

    # The lock order is Plan -> Claim -> Fence -> Dispatch -> Attempt -> Task.
    # Legacy receipt is explicitly denied for v2 entries (B3c-3 owns receipt).
    plan = db.execute(select(ConversationPlan).where(
        ConversationPlan.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    claim = db.execute(select(PlanExecutionClaim).where(
        PlanExecutionClaim.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    assert_dependency_fence_in_session(
        db, conversation_id=binding.conversation_id,
        turn_id=binding.turn_id, fence=fence)
    owner = db.get(Conversation, binding.conversation_id)
    turn = db.get(Turn, binding.turn_id)
    user = (db.execute(select(Message).where(
        Message.id == turn.user_message_id
    ).with_for_update()).scalar_one_or_none()
            if turn is not None and turn.user_message_id else None)
    host = db.get(PrimaryRouteEvidence, binding.turn_id)
    if (plan is None or plan.snapshot != expected_raw
            or claim is None or claim.turn_id != binding.turn_id
            or claim.snapshot != expected_raw
            or owner is None or owner.owner_id != binding.owner_id
            or turn is None or turn.conversation_id != binding.conversation_id
            or turn.request_id != binding.request_id or turn.state != "running"
            or user is None or user.conversation_id != binding.conversation_id
            or user.role != "user" or user.status != "completed"
            or host is None or host.conversation_id != binding.conversation_id
            or host.schema_version != "primary-route-v1"
            or host.route_kind != "plan_resume" or host.disposition != "allow"
            or host.admission_reason != "dependency_revision_resume_prepare_only"
            or host.preturn_snapshot_digest != snapshot_digest(initial_turn_raw)
            or host.approved_request_digest is None
            or host.approved_request_digest != _sha(user.content)):
        _deny("dependency_revision_dispatch_identity_denied")

    anchor = db.execute(select(DependencyRevisionAnchor).where(
        DependencyRevisionAnchor.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    if (anchor is None
            or anchor.revision_turn_id != binding.approved_revision_turn_id):
        _deny("dependency_revision_dispatch_origin_denied")
    approval = db.get(PrimaryRouteEvidence, anchor.revision_turn_id)
    revision_turn = db.get(Turn, anchor.revision_turn_id)
    revision_user = (db.get(Message, revision_turn.user_message_id)
                     if revision_turn is not None and revision_turn.user_message_id else None)
    revision_assistant = (db.get(Message, revision_turn.assistant_message_id)
                          if revision_turn is not None and revision_turn.assistant_message_id else None)
    if (approval is None or approval.conversation_id != binding.conversation_id
            or approval.schema_version != "primary-route-v1"
            or approval.route_kind != "plan_revise"
            or approval.disposition != "allow"
            or approval.admission_reason != "dependency_revision_candidate"
            or approval.approved_revision_digest != anchor.approved_digest
            or revision_turn is None
            or revision_turn.conversation_id != binding.conversation_id
            or revision_turn.state != "partial"
            or revision_turn.failure_code is not None
            or revision_user is None or revision_user.role != "user"
            or revision_user.conversation_id != binding.conversation_id
            or revision_user.status != "completed"
            or revision_assistant is None or revision_assistant.role != "assistant"
            or revision_assistant.conversation_id != binding.conversation_id
            or revision_assistant.status != "partial"):
        _deny("dependency_revision_dispatch_origin_denied")

    checkpoint = _decode(expected_raw)
    approved = _decode(anchor.approved_candidate_json)
    lineage = checkpoint.get("lineage")
    try:
        origin_refs = json.loads(anchor.origin_refs_json)
    except (ValueError, TypeError):
        _deny("dependency_revision_dispatch_origin_denied")
    if (type(lineage) is not dict
            or type(origin_refs) is not list
            or checkpoint != approved
            or checkpoint.get("revision") != 2
            or checkpoint.get("plan_executor_version") != REVISED_EXECUTOR_VERSION
            or revision_fingerprint(approved) != anchor.approved_digest
            or checkpoint.get("lineage") != approved.get("lineage")
            or anchor.parent_snapshot_sha256
                != lineage.get("parent_snapshot_sha256")
            or origin_refs != lineage.get("origin_refs")):
        _deny("dependency_revision_dispatch_origin_denied")
    steps = checkpoint.get("steps")
    if (type(steps) is not list or len(steps) < 2
            or type(checkpoint.get("executions")) is not dict
            or type(checkpoint.get("lineage")) is not dict):
        _deny("dependency_revision_dispatch_lineage_untrusted")
    pending = [s for s in steps if type(s) is dict and s.get("status") == "pending"]
    if (not pending or pending[0].get("id") != binding.step_id
            or any(s.get("status") not in ("completed", "pending")
                   for s in steps if type(s) is dict)
            or any(s.get("status") == "completed" for s in
                   steps[steps.index(pending[0]):])
            or type(binding.step_id) is not int):
        _deny("dependency_revision_dispatch_step_denied")
    target = pending[0]
    if (target.get("expected_tool") != binding.tool_name
            or target.get("tool_version") != binding.tool_version
            or binding.tool_version != PINNED_TOOL_VERSION
            or READ_REGISTRY.version_for(binding.tool_name) != binding.tool_version
            or binding.tool_name not in ("mcp_reference_lookup", "text_stats")):
        _deny("dependency_revision_dispatch_tool_denied")

    # Lock all historical rows and their Attempts BEFORE checking the absence
    # of pending-step dispatches; do not trust the in-memory B3b proof.
    dispatches = db.scalars(select(PlanToolDispatch).where(
        PlanToolDispatch.conversation_id == binding.conversation_id
    ).order_by(PlanToolDispatch.step_id, PlanToolDispatch.id)
        .with_for_update()).all()
    _verify_inherited(db, conversation_id=binding.conversation_id,
                      checkpoint=checkpoint, dispatches=dispatches)
    task = db.execute(select(AgentTaskState).where(
        AgentTaskState.conversation_id == binding.conversation_id
    ).with_for_update()).scalar_one_or_none()
    expected_projection = [{
        "id": s["id"], "status": s["status"],
        "evidence_tool": (s["expected_tool"]
                          if s["status"] == "completed" else None),
    } for s in steps]
    transition = db.scalars(select(AgentStateTransition).where(
        AgentStateTransition.conversation_id == binding.conversation_id,
        AgentStateTransition.reason_code == "dependency_plan_revision_saved",
    )).all()
    completed = [s for s in steps if s["status"] == "completed"]
    if (task is None or task.plan_revision != 2
            or task.state != ("partial" if completed else "planned")
            or _decode_list(task.step_states) != expected_projection
            or len(transition) != 1
            or transition[0].task_id != task.task_id
            or transition[0].revision != task.revision
            or transition[0].entity_type != "task"
            or transition[0].step_id is not None
            or transition[0].attempt_id is not None
            or transition[0].from_state
                not in (None, "planned", "partial", "blocked")
            or transition[0].to_state != task.state):
        _deny("dependency_revision_dispatch_projection_denied")

    try:
        if binding.tool_name == "mcp_reference_lookup":
            term = target["argument_bindings"]["term"]
            checked = ReferenceArgs.model_validate(binding.arguments).model_dump()
            if (term.get("kind") != "literal"
                    or target.get("depends_on") != []
                    or binding.inherited_source_dispatch_id is not None
                    or checked != {"term": term.get("value")}):
                _deny("dependency_revision_dispatch_arguments_denied")
        else:
            ref = target["argument_bindings"]["text"]
            checked = TextStatsArgs.model_validate(binding.arguments).model_dump()
            origin = checkpoint["executions"].get(str(ref["source_step_id"]))
            if (ref.get("kind") != "observation_ref"
                    or ref.get("field") != "definition"
                    or ref["source_step_id"] not in checkpoint["lineage"]["preserved_step_ids"]
                    or ref["source_step_id"] not in target.get("depends_on", [])
                    or type(origin) is not dict or origin.get("plan_revision") != 1
                    or binding.inherited_source_dispatch_id != origin.get("dispatch_id")):
                _deny("dependency_revision_dispatch_source_denied")
            source = next((d for d in dispatches if d.id == origin["dispatch_id"]), None)
            if source is None or source.tool_name != "mcp_reference_lookup":
                _deny("dependency_revision_dispatch_source_denied")
            receipt = _decode(source.receipt_json)
            if (receipt.get("ok") is not True
                    or type(receipt.get("definition")) is not str
                    or checked != {"text": receipt["definition"]}):
                _deny("dependency_revision_dispatch_source_denied")
        if checked != binding.arguments:
            _deny("dependency_revision_dispatch_arguments_denied")
    except (KeyError, TypeError, ValidationError):
        _deny("dependency_revision_dispatch_arguments_denied")
    # One immutable logical step reservation, even after failure or recovery.
    if db.get(DependencyV2StepReservation,
              (binding.conversation_id, binding.step_id)) is not None:
        _deny("dependency_revision_dispatch_duplicate")
    entry_id = str(uuid.uuid4())
    db.add(PlanToolDispatch(
        id=entry_id, conversation_id=binding.conversation_id,
        turn_id=binding.turn_id, step_id=binding.step_id, plan_revision=2,
        worker_generation=fence.generation,
        worker_token_digest=_sha(fence.secret),
        tool_name=binding.tool_name, tool_version=binding.tool_version,
        arguments_json=json.dumps(checked, ensure_ascii=False, sort_keys=True),
        status="dispatch_unknown",
    ))
    # Flush the referenced Dispatch first: without an ORM relationship,
    # SQLAlchemy may INSERT the Reservation first on PostgreSQL. Both flushes
    # remain in the SAME caller-owned transaction and roll back together.
    db.flush()
    db.add(DependencyV2StepReservation(
        conversation_id=binding.conversation_id,
        step_id=binding.step_id, dispatch_id=entry_id,
        turn_id=binding.turn_id, created_at=now(),
    ))
    db.flush()
    return entry_id


def _decode_list(value):
    try:
        item = json.loads(value)
    except (ValueError, TypeError):
        _deny("dependency_revision_dispatch_projection_denied")
    if type(item) is not list:
        _deny("dependency_revision_dispatch_projection_denied")
    return item
