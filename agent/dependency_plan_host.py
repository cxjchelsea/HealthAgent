"""S25-06 Slice C: Host-side *preparation* of new dependency-plan read calls.

This module never grants durable tool authority, begins a Dispatch, invokes
an adapter or commits a checkpoint. The primary Host owns routing; M4 owns
the execution Claim/Journal/CAS. Slice D must use this prepared binding only
after rechecking its snapshot and claim inside the dispatch boundary.
"""
from dataclasses import dataclass
import hashlib
import json

from pydantic import ValidationError

from agent.config import Settings
from agent.dependency_plan_schema import (
    DependencyPlanError, PLAN_SCHEMA_VERSION, PLAN_EXECUTOR_VERSION,
    PINNED_TOOL_VERSION, DependencyStep, LiteralBinding, ObservationBinding,
)
from agent.primary_host_admission_v1 import PrimaryHostDecision
from agent.tool_registry import READ_REGISTRY
from agent.tools import ReferenceArgs
from services.db import (
    Conversation, ConversationPlan, PlanExecutionClaim, Turn,
)
from services.dependency_observation import resolve_checkpointed_definition
from services.evidence_store import arguments_digest


_PLAN_READ_TOOLS = frozenset({"mcp_reference_lookup", "text_stats"})


@dataclass(frozen=True)
class PreparedDependencyRead:
    owner_id: str
    conversation_id: str
    turn_id: str
    request_id: str
    plan_revision: int
    checkpoint_digest: str
    control: str
    step_id: int
    tool_name: str
    tool_version: str
    arguments: dict
    arguments_digest: str
    source_dispatch_id: str | None = None


def _deny(code: str):
    raise DependencyPlanError(code)


def _guard_settings(settings: Settings) -> None:
    if (not settings.s25_06_dependency_plan_enabled
            or settings.route_admission_mode != "guarded"
            or settings.understanding_strategy != "structured"
            or not settings.primary_route_v1_active):
        _deny("dependency_host_disabled")


def _guard_decision(decision: PrimaryHostDecision, control: str) -> None:
    if control not in {"execute_first", "resume"}:
        _deny("dependency_host_control_denied")
    expected_kind = "plan_execute_first" if control == "execute_first" else "plan_resume"
    if (not isinstance(decision, PrimaryHostDecision)
            or decision.disposition != "allow"
            or decision.reason != "primary_v1_plan_execution_candidate"
            or decision.route_kind != expected_kind
            or decision.plan_control != control
            or decision.execution_mode != "read_only"):
        _deny("dependency_host_control_denied")


def _parse_snapshot(raw: str) -> dict:
    try:
        checkpoint = json.loads(raw)
    except (TypeError, ValueError):
        _deny("dependency_invalid_schema")
    if type(checkpoint) is not dict:
        _deny("dependency_invalid_schema")
    if (checkpoint.get("plan_schema_version") != PLAN_SCHEMA_VERSION
            or checkpoint.get("plan_executor_version") != PLAN_EXECUTOR_VERSION):
        _deny("dependency_version_mismatch")
    return checkpoint


def _step_for_read(checkpoint: dict, step_id: int) -> dict:
    """Validate the WHOLE persisted plan, not just its selected step.

    The creation parser accepts only all-pending proposals. This validator is
    limited to the new read-only runtime format and cannot grant authority.
    """
    steps = checkpoint.get("steps")
    executions = checkpoint.get("executions")
    if (set(checkpoint) != {"plan_schema_version", "plan_executor_version",
                           "revision", "next_step_id", "steps", "executions"}
            or type(steps) is not list or not 2 <= len(steps) <= 8
            or type(executions) is not dict
            or type(checkpoint.get("revision")) is not int
            or checkpoint["revision"] != 1):
        _deny("dependency_invalid_schema")
    completed_ids: set[int] = set()
    seen: set[int] = set()
    last_id = 0
    seen_pending = False
    normalized_steps = []
    for raw in steps:
        if type(raw) is not dict:
            _deny("dependency_invalid_schema")
        # Creation strict schema disallows completed states, so check the
        # runtime state separately before validating all other exact fields.
        status = raw.get("status")
        if status not in {"pending", "completed"}:
            _deny("dependency_invalid_schema")
        try:
            parsed = DependencyStep.model_validate({**raw, "status": "pending"})
        except (ValidationError, TypeError, ValueError):
            _deny("dependency_invalid_schema")
        sid = parsed.id
        if sid in seen or sid <= last_id:
            _deny("dependency_invalid_schema")
        if len(parsed.depends_on) != len(set(parsed.depends_on)):
            _deny("dependency_invalid_schema")
        if any(parent >= sid or parent not in seen for parent in parsed.depends_on):
            _deny("dependency_invalid_schema")
        # Read v1 supports ordered, serial, prefix completion only; no
        # skipping a failed earlier step based on forged completed siblings.
        if status == "completed":
            if seen_pending:
                _deny("dependency_invalid_schema")
            completed_ids.add(sid)
        else:
            seen_pending = True
        if (parsed.expected_tool not in _PLAN_READ_TOOLS
                or parsed.tool_version != PINNED_TOOL_VERSION):
            _deny("dependency_tool_version_mismatch")
        if parsed.expected_tool == "mcp_reference_lookup":
            if parsed.depends_on or set(parsed.argument_bindings) != {"term"}:
                _deny("dependency_invalid_schema")
            term = parsed.argument_bindings["term"]
            if not isinstance(term, LiteralBinding):
                _deny("dependency_invalid_schema")
            try:
                checked_args = ReferenceArgs.model_validate(
                    {"term": term.value}).model_dump()
            except ValidationError:
                _deny("dependency_invalid_schema")
            if (checked_args != {"term": term.value}
                    or term.value.casefold() not in term.source_quote.casefold()):
                _deny("dependency_invalid_schema")
        else:
            if set(parsed.argument_bindings) != {"text"}:
                _deny("dependency_invalid_schema")
            bound = parsed.argument_bindings["text"]
            if isinstance(bound, ObservationBinding):
                if (bound.source_step_id not in parsed.depends_on
                        or bound.source_step_id not in seen):
                    _deny("dependency_invalid_schema")
                parent = next(
                    (x for x in normalized_steps
                     if x["id"] == bound.source_step_id), None)
                if (parent is None
                        or parent["expected_tool"] != "mcp_reference_lookup"
                        or bound.field != "definition"):
                    _deny("dependency_invalid_schema")
            elif not isinstance(bound, LiteralBinding):
                _deny("dependency_invalid_schema")
        normalized_steps.append(raw)
        seen.add(sid)
        last_id = sid
    if (type(checkpoint.get("next_step_id")) is not int
            or checkpoint["next_step_id"] != last_id + 1):
        _deny("dependency_invalid_schema")
    if set(executions) != {str(i) for i in completed_ids}:
        _deny("dependency_untrusted_observation")
    for sid in completed_ids:
        info = executions[str(sid)]
        if (type(info) is not dict
                or type(info.get("step_id")) is not int
                or info["step_id"] != sid
                or type(info.get("plan_revision")) is not int
                or info["plan_revision"] != checkpoint["revision"]
                or type(info.get("dispatch_id")) is not str
                or not info["dispatch_id"]
                or type(info.get("resolved_arguments_digest")) is not str
                or len(info["resolved_arguments_digest"]) != 64):
            _deny("dependency_untrusted_observation")
        matching = next(x for x in steps if x["id"] == sid)
        if (info.get("tool_name") != matching["expected_tool"]
                or info.get("tool_version") != matching["tool_version"]):
            _deny("dependency_untrusted_observation")
    pending = [s for s in steps if s["status"] == "pending"]
    if not pending or pending[0]["id"] != step_id:
        _deny("dependency_step_not_ready")
    selected = pending[0]
    if not all(x in completed_ids for x in selected["depends_on"]):
        _deny("dependency_step_not_ready")
    return selected


def bind_dependency_plan_read(
    session_factory, *, settings: Settings, decision: PrimaryHostDecision,
    owner_id: str, conversation_id: str, turn_id: str, request_id: str,
    expected_raw: str, control: str, step_id: int,
) -> PreparedDependencyRead:
    """Bind Host identity + owned running Claim + version-pinned ready step.

    Caller MUST still recheck checkpoint digest and Claim at dispatch time.
    Passing an object from this function directly to Registry is prohibited.
    """
    _guard_settings(settings)
    _guard_decision(decision, control)
    if (not all(type(x) is str and x.strip() for x in
                (owner_id, conversation_id, turn_id, request_id, expected_raw))
            or type(step_id) is not int or step_id < 1):
        _deny("dependency_host_identity_denied")
    with session_factory() as db:
        conversation = db.get(Conversation, conversation_id)
        turn = db.get(Turn, turn_id)
        record = db.get(ConversationPlan, conversation_id)
        claim = db.get(PlanExecutionClaim, conversation_id)
        if (conversation is None or conversation.owner_id != owner_id
                or turn is None or turn.conversation_id != conversation_id
                or turn.request_id != request_id or turn.state != "running"
                or record is None or record.snapshot != expected_raw
                or claim is None or claim.turn_id != turn_id
                or claim.snapshot != expected_raw):
            _deny("dependency_host_claim_denied")
    checkpoint = _parse_snapshot(expected_raw)
    step = _step_for_read(checkpoint, step_id)
    tool_name = step["expected_tool"]
    tool_version = step["tool_version"]
    if READ_REGISTRY.version_for(tool_name) != tool_version:
        _deny("dependency_tool_version_mismatch")
    # Separate allowlist from approved_read_grants(), which only governs
    # ordinary_tool; Registry presence does not authorize saved plans.
    if tool_name not in _PLAN_READ_TOOLS:
        _deny("dependency_host_tool_denied")
    source_dispatch = None
    if tool_name == "mcp_reference_lookup":
        if step.get("depends_on") != []:
            _deny("dependency_step_not_ready")
        bindings = step.get("argument_bindings")
        if type(bindings) is not dict or set(bindings) != {"term"}:
            _deny("dependency_invalid_schema")
        term = bindings["term"]
        if (type(term) is not dict or term.get("kind") != "literal"
                or type(term.get("value")) is not str):
            _deny("dependency_invalid_schema")
        try:
            arguments = ReferenceArgs.model_validate({"term": term["value"]}).model_dump()
        except ValidationError:
            _deny("dependency_invalid_arguments")
    else:
        observed = resolve_checkpointed_definition(
            session_factory, owner_id=owner_id,
            conversation_id=conversation_id, target_step_id=step_id)
        expected_digest = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
        if (observed.checkpoint_digest != expected_digest
                or observed.plan_revision != checkpoint["revision"]):
            _deny("dependency_host_snapshot_changed")
        arguments = observed.arguments
        source_dispatch = observed.source_evidence.dispatch_id
    # One more best-effort preflight check after reading the Observation.
    # Slice D MUST enforce the same check atomically immediately before
    # creating its M4 dispatch_unknown row; this is NOT a dispatch permit.
    with session_factory() as db:
        record = db.get(ConversationPlan, conversation_id)
        claim = db.get(PlanExecutionClaim, conversation_id)
        if (record is None or record.snapshot != expected_raw
                or claim is None or claim.turn_id != turn_id
                or claim.snapshot != expected_raw):
            _deny("dependency_host_snapshot_changed")
    return PreparedDependencyRead(
        owner_id=owner_id, conversation_id=conversation_id,
        turn_id=turn_id, request_id=request_id,
        plan_revision=checkpoint["revision"],
        checkpoint_digest=hashlib.sha256(expected_raw.encode("utf-8")).hexdigest(),
        control=control, step_id=step_id, tool_name=tool_name,
        tool_version=tool_version, arguments=arguments,
        arguments_digest=arguments_digest(arguments),
        source_dispatch_id=source_dispatch,
    )


def plan_read_adapter_schema(binding: PreparedDependencyRead) -> dict:
    """Versioned tool-schema exposure for the single admitted plan step.

    Only describes the pinned, approved tool. Never invokes Registry.
    """
    if (not isinstance(binding, PreparedDependencyRead)
            or binding.tool_name not in _PLAN_READ_TOOLS
            or binding.tool_version != PINNED_TOOL_VERSION
            or READ_REGISTRY.version_for(binding.tool_name) != binding.tool_version):
        _deny("dependency_host_tool_denied")
    exposed = READ_REGISTRY.exposed((binding.tool_name,))
    if len(exposed) != 1 or exposed[0]["function"]["name"] != binding.tool_name:
        _deny("dependency_host_tool_denied")
    return exposed[0]
