"""S25-06 Slice B: read-only resolver for checkpointed dependency observations.

Returns typed arguments and source evidence; NEVER grants tool execution.
No writes, dispatch, tool invocation, or plan checkpoint mutation occur here.
"""
from dataclasses import dataclass
import hashlib
import json

from pydantic import ValidationError

from agent.dependency_plan_schema import (
    DependencyPlanError, PLAN_SCHEMA_VERSION, PLAN_EXECUTOR_VERSION,
    PINNED_TOOL_VERSION,
)
from agent.tool_registry import TextStatsArgs
from agent.tools import ReferenceArgs
from services.db import (
    AgentAttempt, Conversation, ConversationPlan, PlanToolDispatch,
)
from services.evidence_store import arguments_digest, bounded_projection


@dataclass(frozen=True)
class SourceEvidenceRef:
    step_id: int
    dispatch_id: str
    tool_name: str
    tool_version: str
    arguments_digest: str
    field: str


@dataclass(frozen=True)
class ResolvedDependencyRead:
    step_id: int
    tool_name: str
    tool_version: str
    arguments: dict
    arguments_digest: str
    source_evidence: SourceEvidenceRef
    plan_revision: int
    checkpoint_digest: str


def _fail(code: str):
    raise DependencyPlanError(code)


def _object(value):
    if type(value) is not dict:
        _fail("dependency_invalid_schema")
    return value


def _decode(raw):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        _fail("dependency_untrusted_observation")
    return _object(value)


def resolve_checkpointed_definition(
    session_factory, *, owner_id: str, conversation_id: str, target_step_id: int,
) -> ResolvedDependencyRead:
    """Resolve the sole approved MCP definition -> text_stats input pairing.

    Owner identity is checked against the durable Conversation, not the model.
    A checkpointed source Dispatch and exactly matching AgentAttempt are
    necessary but NOT sufficient for invocation; Host/Claim/Tool Guard follow
    in later slices. No raw MCP response or free-form model text is accepted.
    """
    if not all(type(x) is str and x for x in (owner_id, conversation_id)):
        _fail("dependency_untrusted_observation")
    if type(target_step_id) is not int or target_step_id < 1:
        _fail("dependency_invalid_schema")

    with session_factory() as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_id != owner_id:
            _fail("dependency_untrusted_observation")
        saved = db.get(ConversationPlan, conversation_id)
        if saved is None:
            _fail("dependency_invalid_schema")
        # Bind the exact persisted snapshot; future Host admission must compare
        # this digest again under its execution Claim immediately pre-dispatch.
        raw_snapshot = saved.snapshot
        snapshot_digest = hashlib.sha256(raw_snapshot.encode("utf-8")).hexdigest()
        plan = _decode(raw_snapshot)
        if (plan.get("plan_schema_version") != PLAN_SCHEMA_VERSION
                or plan.get("plan_executor_version") != PLAN_EXECUTOR_VERSION):
            _fail("dependency_version_mismatch")
        revision = plan.get("revision")
        if type(revision) is not int or revision < 1:
            _fail("dependency_invalid_schema")
        steps = plan.get("steps")
        executions = plan.get("executions")
        if type(steps) is not list or type(executions) is not dict:
            _fail("dependency_invalid_schema")
        matches = [s for s in steps if type(s) is dict
                   and type(s.get("id")) is int and s["id"] == target_step_id]
        if len(matches) != 1:
            _fail("dependency_invalid_schema")
        target = matches[0]
        if (target.get("status") != "pending"
                or target.get("expected_tool") != "text_stats"
                or target.get("tool_version") != PINNED_TOOL_VERSION):
            _fail("dependency_version_mismatch")
        depends = target.get("depends_on")
        bindings = target.get("argument_bindings")
        if (type(depends) is not list or type(bindings) is not dict
                or any(type(item) is not int for item in depends)
                or len(depends) != len(set(depends))):
            _fail("dependency_invalid_schema")
        if set(bindings) != {"text"}:
            _fail("dependency_invalid_schema")
        binding = _object(bindings["text"])
        if binding.get("kind") != "observation_ref" or binding.get("field") != "definition":
            _fail("dependency_output_not_allowed")
        source_id = binding.get("source_step_id")
        if (type(source_id) is not int or source_id >= target_step_id
                or source_id not in depends):
            _fail("dependency_source_not_dependency")
        parents = [s for s in steps if type(s) is dict
                   and type(s.get("id")) is int and s["id"] == source_id]
        if len(parents) != 1:
            _fail("dependency_unknown_step")
        parent = parents[0]
        if (parent.get("status") != "completed"
                or parent.get("expected_tool") != "mcp_reference_lookup"
                or parent.get("tool_version") != PINNED_TOOL_VERSION):
            _fail("dependency_untrusted_observation")
        parent_bindings = parent.get("argument_bindings")
        if type(parent_bindings) is not dict or set(parent_bindings) != {"term"}:
            _fail("dependency_untrusted_observation")
        parent_term = _object(parent_bindings["term"])
        if (parent_term.get("kind") != "literal"
                or type(parent_term.get("value")) is not str):
            _fail("dependency_untrusted_observation")
        record = _object(executions.get(str(source_id)))
        dispatch_id = record.get("dispatch_id")
        if type(dispatch_id) is not str or not dispatch_id:
            _fail("dependency_untrusted_observation")
        if (record.get("step_id") != source_id
                or type(record.get("plan_revision")) is not int
                or record["plan_revision"] != revision
                or record.get("tool_name") != "mcp_reference_lookup"
                or record.get("tool_version") != PINNED_TOOL_VERSION):
            _fail("dependency_untrusted_observation")
        dispatch = db.get(PlanToolDispatch, dispatch_id)
        if (dispatch is None or dispatch.conversation_id != conversation_id
                or dispatch.step_id != source_id
                or dispatch.tool_name != "mcp_reference_lookup"
                or dispatch.tool_version != PINNED_TOOL_VERSION
                or dispatch.status != "checkpointed"):
            _fail("dependency_untrusted_observation")
        args = _decode(dispatch.arguments_json)
        try:
            validated_source = ReferenceArgs.model_validate(args).model_dump()
        except ValidationError:
            _fail("dependency_untrusted_observation")
        if (validated_source != args
                or args != {"term": parent_term["value"]}):
            _fail("dependency_untrusted_observation")
        digest = arguments_digest(args)
        if record.get("resolved_arguments_digest") != digest:
            _fail("dependency_untrusted_observation")
        attempt = db.get(AgentAttempt, dispatch_id)
        if (attempt is None or attempt.conversation_id != conversation_id
                or attempt.turn_id != dispatch.turn_id
                or attempt.step_id != source_id
                or attempt.status != "succeeded"
                or attempt.tool_name != dispatch.tool_name
                or attempt.tool_version != dispatch.tool_version
                or attempt.arguments_digest != digest):
            _fail("dependency_untrusted_observation")
        receipt = _decode(dispatch.receipt_json)
        if receipt.get("ok") is not True:
            _fail("dependency_untrusted_observation")
        if receipt.get("definition_availability") == "truncated":
            _fail("dependency_observation_truncated")
        definition = receipt.get("definition")
        if type(definition) is not str or not definition:
            _fail("dependency_untrusted_observation")
        # Require the persisted Attempt to agree with the precise bounded
        # receipt, not a fabricated success-like projection.
        projection = _decode(attempt.result_projection)
        expected_projection = bounded_projection("mcp_reference_lookup", receipt)
        if (projection.get("ok") is not True
                or projection != expected_projection):
            _fail("dependency_untrusted_observation")
        if bounded_projection("mcp_reference_lookup", receipt).get("definition") != definition:
            _fail("dependency_observation_truncated")
        try:
            validated = TextStatsArgs.model_validate({"text": definition})
        except ValidationError:
            _fail("dependency_argument_type_mismatch")
        concrete = validated.model_dump()
        return ResolvedDependencyRead(
            step_id=target_step_id, tool_name="text_stats",
            tool_version=PINNED_TOOL_VERSION, arguments=concrete,
            arguments_digest=arguments_digest(concrete),
            source_evidence=SourceEvidenceRef(
                step_id=source_id, dispatch_id=dispatch_id,
                tool_name=dispatch.tool_name, tool_version=dispatch.tool_version,
                arguments_digest=digest, field="definition"),
            plan_revision=revision,
            checkpoint_digest=snapshot_digest,
        )
