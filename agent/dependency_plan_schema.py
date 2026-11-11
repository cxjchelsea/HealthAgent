"""S25-06 Slice A: pure, non-authorizing dependency-plan creation contract.

No Host admission, tool invocation, database access, checkpoint migration or
runtime plan execution takes place here. A successful parse is NOT permission
to create a plan or dispatch a tool.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError


PLAN_SCHEMA_VERSION = "dependency-plan-v1"
PLAN_EXECUTOR_VERSION = "dependency-plan-read-v1"
PINNED_TOOL_VERSION = "1.0.0"
MAX_PLAN_STEPS = 8

# Explicitly limited to an existing, trusted, read-only source/target pair.
_LITERAL_INPUTS = {
    "mcp_reference_lookup": {"term": (1, 80)},
    "text_stats": {"text": (1, 4000)},
}
_ALLOWED_SOURCE_FIELDS = {
    ("mcp_reference_lookup", PINNED_TOOL_VERSION): {"definition": str},
}
_SUPPORTED_TOOLS = frozenset(_LITERAL_INPUTS)

# Explicit invocation syntax has a stronger meaning than a bare identifier:
# "调用 foo_bar" names an operation; "查询 foo_bar 的定义" names DATA.
# This is only an exact, source-bound contract check of user-named tool calls;
# the Primary model remains the sole natural-language intent classifier.
# Do not blacklist all snake_case tokens (they can be legitimate lookup terms).
_OPERATION_NAME = r"([a-z][a-z0-9]*(?:_[a-z0-9]+)+)"
_EXPLICIT_TOOL_CALLS = (
    re.compile(r"(?<![A-Za-z])(?:调用|执行|call|invoke)\s*[\x60'\"“”]?\s*" + _OPERATION_NAME
               + r"(?![A-Za-z0-9_])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_])(?:tool|function)\s*[:=]\s*[\x60'\"“”]?\s*"
               + _OPERATION_NAME + r"(?![A-Za-z0-9_])", re.IGNORECASE),
)


def validate_explicit_operation_identifiers(source_message: str) -> None:
    """Reject only unsupported *explicitly invoked* tool identifiers.

    Bare snake_case lookup targets are ordinary user data. This limited
    syntactic safeguard cannot determine generic semantic goal equivalence.
    """
    invoked = {
        match.group(1)
        for pattern in _EXPLICIT_TOOL_CALLS
        for match in pattern.finditer(source_message)
    }
    if invoked - _SUPPORTED_TOOLS:
        raise DependencyPlanError("dependency_unsupported_operation_identifier")



class DependencyPlanError(ValueError):
    """Stable public failure code without untrusted message/observation data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LiteralBinding(_Strict):
    kind: Literal["literal"]
    value: str = Field(min_length=1, max_length=4000)
    source_quote: str = Field(min_length=1, max_length=400)


class ObservationBinding(_Strict):
    kind: Literal["observation_ref"]
    source_step_id: StrictInt = Field(ge=1)
    field: Literal["definition"]


InputBinding = Annotated[
    LiteralBinding | ObservationBinding, Field(discriminator="kind")
]


class DependencyStep(_Strict):
    id: StrictInt = Field(ge=1)
    goal: str = Field(min_length=1, max_length=160)
    depends_on: list[StrictInt] = Field(max_length=MAX_PLAN_STEPS)
    status: Literal["pending"]
    expected_tool: Literal["mcp_reference_lookup", "text_stats"]
    tool_version: Literal["1.0.0"]
    argument_bindings: dict[str, InputBinding]


class DependencyPlanCreation(_Strict):
    """Only the initial, entirely pending proposal. NOT a restored run state."""

    plan_schema_version: Literal["dependency-plan-v1"]
    plan_executor_version: Literal["dependency-plan-read-v1"]
    revision: Literal[1]
    next_step_id: StrictInt = Field(ge=3, le=MAX_PLAN_STEPS + 1)
    steps: list[DependencyStep] = Field(min_length=2, max_length=MAX_PLAN_STEPS)
    executions: dict


def classify_plan_format(snapshot: object) -> Literal["legacy", "dependency-plan-v1"]:
    """Classify only. Legacy still needs the existing guarded legacy parser.

    A format without a version but with new-format indicators is never
    silently routed into legacy execution.
    """
    if not isinstance(snapshot, dict):
        raise DependencyPlanError("dependency_invalid_schema")
    if "plan_schema_version" not in snapshot:
        if {"plan_executor_version", "executions"}.intersection(snapshot):
            raise DependencyPlanError("dependency_invalid_schema")
        steps = snapshot.get("steps", [])
        if isinstance(steps, list) and any(
            isinstance(step, dict)
            and ({"depends_on", "argument_bindings"} & set(step))
            for step in steps
        ):
            raise DependencyPlanError("dependency_invalid_schema")
        return "legacy"
    if snapshot["plan_schema_version"] != PLAN_SCHEMA_VERSION:
        raise DependencyPlanError("dependency_version_mismatch")
    return PLAN_SCHEMA_VERSION


def validate_dependency_graph(plan: DependencyPlanCreation) -> None:
    """Require stable ascending topological IDs, rejecting unknown/cyclic links."""
    seen: set[int] = set()
    last_id = 0
    for step in plan.steps:
        sid = step.id
        if sid in seen or sid <= last_id:
            raise DependencyPlanError("dependency_invalid_schema")
        if len(step.depends_on) != len(set(step.depends_on)):
            raise DependencyPlanError("dependency_duplicate_edge")
        for parent_id in step.depends_on:
            if parent_id == sid:
                raise DependencyPlanError("dependency_cycle")
            if parent_id not in seen:
                # Forward references are intentionally disallowed, so a cycle
                # or a missing/forward step cannot reach the dispatcher.
                if any(s.id == parent_id for s in plan.steps):
                    raise DependencyPlanError("dependency_order_invalid")
                raise DependencyPlanError("dependency_unknown_step")
        seen.add(sid)
        last_id = sid
    if plan.next_step_id != last_id + 1:
        raise DependencyPlanError("dependency_invalid_schema")


def validate_binding_graph(plan: DependencyPlanCreation, source_message: str) -> None:
    """Check candidate input provenance and declared output->input compatibility.

    This validates a *proposal*, not real observations or runtime permissions.
    Runtime must independently validate concrete arguments and evidence.
    """
    if not isinstance(source_message, str) or not source_message.strip():
        raise DependencyPlanError("dependency_source_unbound")
    steps_by_id = {step.id: step for step in plan.steps}
    for step in plan.steps:
        if step.expected_tool not in _SUPPORTED_TOOLS:
            raise DependencyPlanError("dependency_tool_not_allowed")
        if step.tool_version != PINNED_TOOL_VERSION:
            raise DependencyPlanError("dependency_version_mismatch")
        required = _LITERAL_INPUTS[step.expected_tool]
        if set(step.argument_bindings) != set(required):
            raise DependencyPlanError("dependency_invalid_arguments")
        for param, binding in step.argument_bindings.items():
            minimum, maximum = required[param]
            if isinstance(binding, LiteralBinding):
                if not (minimum <= len(binding.value) <= maximum):
                    raise DependencyPlanError("dependency_invalid_arguments")
                quote = binding.source_quote.strip()
                if (not quote or quote.casefold() not in source_message.casefold()
                        or binding.value.casefold() not in quote.casefold()):
                    raise DependencyPlanError("dependency_source_unbound")
            else:
                if binding.source_step_id not in step.depends_on:
                    raise DependencyPlanError("dependency_source_not_dependency")
                parent = steps_by_id.get(binding.source_step_id)
                if parent is None:
                    raise DependencyPlanError("dependency_unknown_step")
                fields = _ALLOWED_SOURCE_FIELDS.get(
                    (parent.expected_tool, parent.tool_version), {})
                if fields.get(binding.field) is not str:
                    raise DependencyPlanError("dependency_output_not_allowed")
                # Target schemas accept a string, but the actual length/type
                # and complete Receipt MUST be revalidated at execution time.
                if step.expected_tool != "text_stats" or param != "text":
                    raise DependencyPlanError("dependency_argument_type_mismatch")


def parse_new_plan(snapshot: object, *, source_message: str) -> DependencyPlanCreation:
    """Strict initial creation proposal only; never parses an execution checkpoint."""
    if classify_plan_format(snapshot) != PLAN_SCHEMA_VERSION:
        raise DependencyPlanError("dependency_version_mismatch")
    try:
        plan = DependencyPlanCreation.model_validate(snapshot)
    except (ValidationError, ValueError, TypeError) as exc:
        raise DependencyPlanError("dependency_invalid_schema") from exc
    if plan.executions:
        raise DependencyPlanError("dependency_execution_not_allowed")
    validate_dependency_graph(plan)
    validate_explicit_operation_identifiers(source_message)
    validate_binding_graph(plan, source_message)
    return plan


def creation_ready_step_ids(plan: DependencyPlanCreation) -> tuple[int, ...]:
    """Pure readiness preview; NEVER a tool execution grant."""
    return tuple(step.id for step in plan.steps if not step.depends_on)
