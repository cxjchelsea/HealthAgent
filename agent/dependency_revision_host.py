"""B3b internal-only, revision-2 prepared read (NO dispatch or execution).

The Host decision is a semantic proposal admission, not a tool permit. This
read-only binder requires an existing current-Turn Claim and rechecks B3a's
physical origin proof. B3c must re-verify under Plan/Claim/Dispatch locks.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agent.config import Settings
from agent.dependency_plan_schema import DependencyPlanError, PINNED_TOOL_VERSION
from agent.dependency_revision_candidate import REVISED_EXECUTOR_VERSION
from agent.primary_host_admission_v1 import PrimaryHostDecision
from agent.tool_registry import READ_REGISTRY, TextStatsArgs
from agent.tools import ReferenceArgs
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, Message,
    PlanExecutionClaim, PlanToolDispatch, PrimaryRouteEvidence, Turn,
)
from services.dependency_revision_lineage import verify_revised_lineage
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_route_evidence import snapshot_digest


@dataclass(frozen=True)
class PreparedRevisedRead:
    """Inert prepared data; deliberately NOT PreparedDependencyRead."""
    owner_id: str
    conversation_id: str
    turn_id: str
    request_id: str
    plan_revision: int
    checkpoint_digest: str
    approved_revision_turn_id: str
    step_id: int
    tool_name: str
    tool_version: str
    arguments: dict
    arguments_digest: str
    inherited_source_dispatch_id: str | None


def _deny(code: str):
    raise DependencyPlanError(code)


def bind_revised_plan_read(
    session_factory, *, settings: Settings, decision: PrimaryHostDecision,
    owner_id: str, conversation_id: str, turn_id: str, request_id: str,
    expected_raw: str, initial_turn_raw: str, step_id: int,
) -> PreparedRevisedRead:
    """Bind ONE first-pending v2 read without invoking or reserving a tool.

    An existing durable Claim and Primary receipt are prerequisites. The
    returned value is NOT accepted by the S25-06 dispatch or checkpoint API.
    """
    if (not settings.s25_06_dependency_plan_enabled
            or not settings.s25_07_revision_resume_prepare_enabled
            or not settings.primary_route_v1_active
            or settings.route_admission_mode != "guarded"
            or settings.understanding_strategy != "structured"
            or not isinstance(decision, PrimaryHostDecision)
            or decision.disposition != "allow"
            or decision.route_kind != "plan_resume"
            or decision.reason != "dependency_revision_resume_prepare_only"
            or decision.execution_mode != "read_only"
            or decision.plan_control != "resume"
            or not all(type(x) is str and x.strip() for x in (
                owner_id, conversation_id, turn_id, request_id,
                expected_raw, initial_turn_raw))
            or initial_turn_raw != expected_raw
            or type(step_id) is not int or step_id < 1):
        _deny("dependency_revision_resume_not_authorized")
    try:
        checkpoint = json.loads(expected_raw)
        if (type(checkpoint) is not dict
                or checkpoint.get("plan_executor_version") != REVISED_EXECUTOR_VERSION
                or type(checkpoint.get("steps")) is not list
                or checkpoint.get("revision") != 2):
            _deny("dependency_revision_resume_version_denied")
        pending = [step for step in checkpoint["steps"]
                   if type(step) is dict and step.get("status") == "pending"]
        if (not pending or pending[0].get("id") != step_id
                or type(pending[0].get("id")) is not int):
            _deny("dependency_revision_resume_not_ready")
    except (TypeError, ValueError, AttributeError):
        _deny("dependency_revision_resume_invalid_snapshot")
    # This read-only proof checks real historical Dispatch/Attempt/Host/TaskState
    # rather than accepting the model's plan JSON as execution history.
    proof = verify_revised_lineage(
        session_factory, owner_id=owner_id,
        conversation_id=conversation_id, expected_raw=expected_raw)
    if proof.next_step_id != step_id:
        _deny("dependency_revision_resume_not_ready")
    with session_factory() as db:
        owner = db.get(Conversation, conversation_id)
        turn = db.get(Turn, turn_id)
        saved = db.get(ConversationPlan, conversation_id)
        claim = db.get(PlanExecutionClaim, conversation_id)
        admission = db.get(PrimaryRouteEvidence, turn_id)
        user = (db.get(Message, turn.user_message_id)
                if turn is not None and turn.user_message_id else None)
        if (owner is None or owner.owner_id != owner_id
                or turn is None or turn.conversation_id != conversation_id
                or turn.request_id != request_id or turn.state != "running"
                or user is None or user.conversation_id != conversation_id
                or user.role != "user" or user.status != "completed"
                or decision.approved_request_digest is None
                or decision.approved_request_digest != hashlib.sha256(
                    user.content.encode("utf-8")).hexdigest()
                or saved is None or saved.snapshot != expected_raw
                or claim is None or claim.turn_id != turn_id
                or claim.snapshot != expected_raw
                or admission is None or admission.conversation_id != conversation_id
                or admission.schema_version != "primary-route-v1"
                or admission.route_kind != "plan_resume"
                or admission.disposition != "allow"
                or admission.admission_reason
                    != "dependency_revision_resume_prepare_only"
                or admission.preturn_snapshot_digest
                    != snapshot_digest(initial_turn_raw)):
            _deny("dependency_revision_resume_identity_denied")
        selected = pending[0]
        name = selected["expected_tool"]
        version = selected["tool_version"]
        if (name not in {"mcp_reference_lookup", "text_stats"}
                or version != PINNED_TOOL_VERSION
                or READ_REGISTRY.version_for(name) != version):
            _deny("dependency_revision_resume_tool_denied")
        source_id = None
        if name == "mcp_reference_lookup":
            binding = selected["argument_bindings"]["term"]
            try:
                checked = ReferenceArgs.model_validate({
                    "term": binding["value"]}).model_dump()
            except (ValueError, TypeError, KeyError):
                _deny("dependency_revision_resume_arguments_denied")
            if (binding["kind"] != "literal" or selected["depends_on"]
                    or checked != {"term": binding["value"]}):
                _deny("dependency_revision_resume_arguments_denied")
        else:
            binding = selected["argument_bindings"]["text"]
            if (binding.get("kind") != "observation_ref"
                    or binding.get("field") != "definition"
                    or binding.get("source_step_id") not in
                        proof.preserved_step_ids):
                _deny("dependency_revision_resume_source_denied")
            origin = checkpoint["executions"].get(str(binding["source_step_id"]))
            if type(origin) is not dict or origin.get("plan_revision") != 1:
                _deny("dependency_revision_resume_source_denied")
            source_id = origin.get("dispatch_id")
            source = db.get(PlanToolDispatch, source_id)
            attempt = db.get(AgentAttempt, source_id)
            if (source is None or source.conversation_id != conversation_id
                    or source.step_id != binding["source_step_id"]
                    or source.status != "checkpointed"
                    or source.tool_name != "mcp_reference_lookup"
                    or source.tool_version != PINNED_TOOL_VERSION
                    or attempt is None or attempt.conversation_id != conversation_id
                    or attempt.turn_id != source.turn_id
                    or attempt.step_id != source.step_id
                    or attempt.status != "succeeded"):
                _deny("dependency_revision_resume_source_denied")
            try:
                original = json.loads(source.receipt_json)
                original_args = json.loads(source.arguments_json)
                original_projection = json.loads(attempt.result_projection)
            except (ValueError, TypeError):
                _deny("dependency_revision_resume_source_denied")
            if (type(original) is not dict
                    or type(original_args) is not dict
                    or type(original_projection) is not dict
                    or original.get("ok") is not True
                    or original.get("definition_availability") == "truncated"
                    or original_projection
                        != bounded_projection("mcp_reference_lookup", original)
                    or origin.get("resolved_arguments_digest")
                        != arguments_digest(original_args)
                    or attempt.arguments_digest != arguments_digest(original_args)
                    or type(original.get("definition")) is not str
                    or not original["definition"]):
                _deny("dependency_revision_resume_source_denied")
            try:
                checked = TextStatsArgs.model_validate({
                    "text": original["definition"]}).model_dump()
            except (ValueError, TypeError):
                _deny("dependency_revision_resume_source_denied")
        # Read-only proof is not a pre-dispatch CAS. Recheck current Claim
        # before returning; B3c MUST repeat the whole proof under locks.
        if (db.get(ConversationPlan, conversation_id).snapshot != expected_raw
                or db.get(PlanExecutionClaim, conversation_id).snapshot != expected_raw):
            _deny("dependency_revision_resume_snapshot_changed")
    return PreparedRevisedRead(
        owner_id=owner_id, conversation_id=conversation_id,
        turn_id=turn_id, request_id=request_id,
        plan_revision=2,
        checkpoint_digest=hashlib.sha256(expected_raw.encode("utf-8")).hexdigest(),
        approved_revision_turn_id=proof.revision_turn_id,
        step_id=step_id, tool_name=name, tool_version=version,
        arguments=checked, arguments_digest=arguments_digest(checked),
        inherited_source_dispatch_id=source_id)
