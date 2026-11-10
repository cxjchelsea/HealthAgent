"""S25-06 Slice D: Host-owned, new-format durable pre-dispatch intent.

Only the existing M4 journal table is used. This is invoked from a trusted
runner after Slice C admission; immutable Primary evidence and an active
execution Claim are mandatory. No model/remote MCP payload may grant access.
"""
import hashlib
import json

from pydantic import ValidationError
from sqlalchemy import select

from agent.dependency_plan_host import (
    PreparedDependencyRead, _step_for_read, _verify_completed_records,
)
from agent.dependency_plan_schema import DependencyPlanError
from agent.tool_registry import READ_REGISTRY, TextStatsArgs
from agent.tools import ReferenceArgs
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn,
)
from services.evidence_store import arguments_digest, bounded_projection
from services.plan_dispatch import UNRESOLVED_DISPATCH_STATUSES
from services.primary_route_evidence import snapshot_digest


def _deny(code):
    raise DependencyPlanError(code)


def begin_dependency_dispatch_in_session(
    db, *, binding: PreparedDependencyRead, expected_raw: str,
    initial_turn_raw: str,
) -> str:
    """Check authorized, current facts then persist dispatch_unknown atomically.

    IMPORTANT: tool calls happen AFTER this transaction commits; missing
    receipts mean unknown outcome and MUST NOT be automatically retried.
    """
    if (type(binding) is not PreparedDependencyRead
            or type(expected_raw) is not str
            or type(initial_turn_raw) is not str
            or hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
                != binding.checkpoint_digest
            or arguments_digest(binding.arguments) != binding.arguments_digest):
        _deny("dependency_execution_binding_invalid")
    # Conforms to established lock order: Plan -> Claim -> Dispatch.
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
            or expected_route is None
            or primary is None or primary.conversation_id != binding.conversation_id
            or primary.schema_version != "primary-route-v1"
            or primary.route_kind != expected_route
            or primary.disposition != "allow"
            or primary.admission_reason != "primary_v1_plan_execution_candidate"
            or primary.preturn_snapshot_digest
                != snapshot_digest(initial_turn_raw)):
        _deny("dependency_execution_claim_conflict")
    try:
        checkpoint = json.loads(expected_raw)
    except (ValueError, TypeError):
        _deny("dependency_invalid_schema")
    if type(checkpoint) is not dict:
        _deny("dependency_invalid_schema")
    if (checkpoint.get("plan_schema_version") != "dependency-plan-v1"
            or checkpoint.get("plan_executor_version") != "dependency-plan-read-v1"
            or checkpoint.get("revision") != binding.plan_revision):
        _deny("dependency_version_mismatch")
    step = _step_for_read(checkpoint, binding.step_id)
    _verify_completed_records(
        db, checkpoint=checkpoint, conversation_id=binding.conversation_id)
    if binding.control == "execute_first" and binding.step_id != checkpoint["steps"][0]["id"]:
        _deny("dependency_execution_scope_denied")
    if (step.get("expected_tool") != binding.tool_name
            or step.get("tool_version") != binding.tool_version
            or READ_REGISTRY.version_for(binding.tool_name) != binding.tool_version):
        _deny("dependency_tool_version_mismatch")
    try:
        if binding.tool_name == "mcp_reference_lookup":
            checked = ReferenceArgs.model_validate(binding.arguments).model_dump()
            term = step["argument_bindings"].get("term")
            if (type(term) is not dict or term.get("kind") != "literal"
                    or binding.source_dispatch_id is not None
                    or checked != {"term": term.get("value")}):
                _deny("dependency_execution_binding_invalid")
        elif binding.tool_name == "text_stats":
            checked = TextStatsArgs.model_validate(binding.arguments).model_dump()
            ref = step["argument_bindings"].get("text")
            if (type(ref) is not dict or ref.get("kind") != "observation_ref"
                    or ref.get("field") != "definition"
                    or type(ref.get("source_step_id")) is not int):
                _deny("dependency_execution_binding_invalid")
            source_record = checkpoint["executions"].get(str(ref["source_step_id"]))
            if (type(source_record) is not dict
                    or source_record.get("dispatch_id") != binding.source_dispatch_id):
                _deny("dependency_untrusted_observation")
            source = db.get(PlanToolDispatch, binding.source_dispatch_id)
            attempt = db.get(AgentAttempt, binding.source_dispatch_id)
            if (source is None or source.conversation_id != binding.conversation_id
                    or source.step_id != ref["source_step_id"]
                    or source.status != "checkpointed"
                    or source.tool_name != "mcp_reference_lookup"
                    or attempt is None or attempt.conversation_id != binding.conversation_id
                    or attempt.turn_id != source.turn_id
                    or attempt.status != "succeeded"):
                _deny("dependency_untrusted_observation")
            try:
                receipt = json.loads(source.receipt_json)
            except (ValueError, TypeError):
                _deny("dependency_untrusted_observation")
            if (type(receipt) is not dict or receipt.get("ok") is not True
                    or type(receipt.get("definition")) is not str
                    or checked != {"text": receipt["definition"]}
                    or bounded_projection("mcp_reference_lookup", receipt).get("definition")
                        != receipt["definition"]):
                _deny("dependency_untrusted_observation")
        else:
            _deny("dependency_host_tool_denied")
    except ValidationError:
        _deny("dependency_invalid_arguments")
    if checked != binding.arguments:
        _deny("dependency_invalid_arguments")
    if db.scalar(select(PlanToolDispatch.id).where(
            PlanToolDispatch.conversation_id == binding.conversation_id,
            PlanToolDispatch.status.in_(UNRESOLVED_DISPATCH_STATUSES))):
        _deny("dependency_execution_unreconciled")
    if db.scalar(select(PlanToolDispatch.id).where(
            PlanToolDispatch.conversation_id == binding.conversation_id,
            PlanToolDispatch.step_id == binding.step_id)):
        _deny("dependency_execution_duplicate_dispatch")
    entry = PlanToolDispatch(
        conversation_id=binding.conversation_id, turn_id=binding.turn_id,
        step_id=binding.step_id, tool_name=binding.tool_name,
        tool_version=binding.tool_version,
        arguments_json=json.dumps(checked, ensure_ascii=False, sort_keys=True),
        status="dispatch_unknown",
    )
    db.add(entry)
    db.flush()
    return entry.id
