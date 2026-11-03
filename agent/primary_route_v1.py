"""Primary-route-v1: ONE semantic proposal, not an execution authority.

Work package 1 contract. Host validates source quotes and trusted context;
it never treats the model's declared intent as authorization to mutate state.
This module deliberately has no tool, database, or AgentLoop imports.
"""
import json
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from agent.route_shadow import EvidenceSpan
from agent.m5_model_call_metrics import attributed

ROUTE_SCHEMA_VERSION = "primary-route-v1"
RouteKind = Literal[
    "plan_create", "plan_revise", "plan_execute_first", "plan_resume",
    "plan_cancel", "plan_missing", "ordinary_tool", "respond",
    "clarify", "clarify_response", "reject",
]
Scope = Literal["none", "first_step", "remaining_steps", "all_steps"]
Effect = Literal["none", "read_only", "mutation_requested"]

# Version-specific migration ONLY: never silently reinterpret M5 'other'.
LEGACY_ROUTE_ADAPTER = {
    "create": "plan_create", "revise": "plan_revise",
    "execute_first": "plan_execute_first", "resume": "plan_resume",
    "cancel": "plan_cancel",
    "plan_create": "plan_create", "plan_revise": "plan_revise",
    "plan_execute_first": "plan_execute_first", "plan_resume": "plan_resume",
    "plan_cancel": "plan_cancel", "plan_missing": "plan_missing",
}


class PrimaryRouteRejected(ValueError):
    """Safe reason codes only; no user message or raw model content."""

    def __init__(self, code: str, *, validation_issues: list[dict] | None = None,
                 route_triple: dict | None = None):
        super().__init__(code)
        self.validation_issues = validation_issues or []
        self.route_triple = route_triple


# Include only contractual field names and validator categories. Neither raw
# model output nor Pydantic messages/ctx/input are allowed into diagnostics.
_ROUTE_DIAGNOSTIC_FIELDS = frozenset({
    "schema_version", "route_kind", "quote", "execution_scope",
    "effect_intent", "steps", "step_number", "clarification_question",
    "revision_old_argument", "revision_new_argument", "write_tool_name",
    "write_arguments", "pending_write_tool_name", "goal", "tool_name",
    "arguments",
})
_ROUTE_DIAGNOSTIC_TYPES = frozenset({
    "missing", "extra_forbidden", "literal_error", "string_type",
    "list_type", "dict_type", "model_type", "int_type", "int_parsing",
    "greater_than_equal", "less_than_equal", "string_too_short",
    "string_too_long", "too_long", "too_short", "value_error",
    "json_invalid", "json_type", "none_required",
    # Explicit validator codes, no raw model output or Pydantic ctx.
    "primary_route_scope_effect_mismatch",
    "primary_route_steps_mismatch",
    "primary_route_revision_needs_step",
    "primary_route_unexpected_step",
    "primary_route_unexpected_revision_arguments",
    "primary_route_write_contract_missing",
    "primary_route_unexpected_write_contract",
    "primary_route_pending_write_invalid",
    "primary_route_clarification_mismatch",
})


def safe_route_triple(raw: str, issues: list[dict]) -> dict | None:
    """Only fixed enum labels; never surface raw provider text or arguments."""
    if not any(x["category"] == "primary_route_scope_effect_mismatch"
               for x in issues):
        return None
    try:
        proposed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(proposed, dict):
        return None
    candidates = {
        "route_kind": frozenset(get_args(RouteKind)),
        "execution_scope": frozenset(get_args(Scope)),
        "effect_intent": frozenset(get_args(Effect)),
    }
    return {
        field: value if isinstance(value := proposed.get(field), str)
        and value in valid else "invalid"
        for field, valid in candidates.items()
    }


def safe_route_validation_issues(exc: ValidationError) -> list[dict]:
    """Bounded schema diagnostics; never include values, quotes or messages."""
    result = []
    for error in exc.errors(include_input=False, include_url=False)[:8]:
        loc = error.get("loc", ())
        fields = [item for item in loc
                  if isinstance(item, str) and item in _ROUTE_DIAGNOSTIC_FIELDS]
        kind = error.get("type")
        result.append({
            "field": ".".join(fields[:3]) if fields else "root",
            "category": kind if kind in _ROUTE_DIAGNOSTIC_TYPES else "other",
        })
    return result


class StrictRouteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedStep(StrictRouteModel):
    goal: str = Field(min_length=1, max_length=400)
    tool_name: Literal["calculate", "mcp_reference_lookup"]
    arguments: dict
    quote: str = Field(min_length=1, max_length=400)


class RouteSignal(StrictRouteModel):
    schema_version: Literal["primary-route-v1"]
    route_kind: RouteKind
    quote: str = Field(min_length=1, max_length=400)
    execution_scope: Scope = "none"
    effect_intent: Effect = "none"
    steps: list[ProposedStep] = Field(default_factory=list, max_length=8)
    step_number: int | None = Field(default=None, ge=1, le=8)
    clarification_question: str | None = Field(default=None, min_length=1, max_length=500)
    revision_old_argument: str | None = Field(default=None, min_length=1, max_length=400)
    revision_new_argument: str | None = Field(default=None, min_length=1, max_length=400)
    write_tool_name: Literal["create_task", "save_memory"] | None = None
    write_arguments: dict | None = None
    pending_write_tool_name: Literal["create_task"] | None = None

    @model_validator(mode="after")
    def match_operation(self):
        allowed = {
            "plan_create": ({"none"}, {"mutation_requested"}),
            "plan_revise": ({"none"}, {"mutation_requested"}),
            "plan_execute_first": ({"first_step"}, {"read_only"}),
            "plan_resume": ({"remaining_steps", "all_steps"}, {"read_only"}),
            "plan_cancel": ({"none"}, {"mutation_requested"}),
            "plan_missing": ({"none"}, {"none"}),
            "ordinary_tool": ({"none"}, {"read_only", "mutation_requested"}),
            "respond": ({"none"}, {"none"}),
            "clarify": ({"none"}, {"none"}),
            "clarify_response": ({"none"}, {"none"}),
            "reject": ({"none"}, {"none"}),
        }
        scopes, effects = allowed[self.route_kind]
        if self.execution_scope not in scopes or self.effect_intent not in effects:
            raise PydanticCustomError("primary_route_scope_effect_mismatch", "primary_route_scope_effect_mismatch")
        if (self.route_kind == "plan_create" and not 2 <= len(self.steps) <= 8) or (self.route_kind != "plan_create" and self.steps):
            raise PydanticCustomError("primary_route_steps_mismatch", "primary_route_steps_mismatch")
        if self.route_kind == "plan_revise" and self.step_number is None:
            raise PydanticCustomError("primary_route_revision_needs_step", "primary_route_revision_needs_step")
        if self.route_kind != "plan_revise" and self.step_number is not None:
            raise PydanticCustomError("primary_route_unexpected_step", "primary_route_unexpected_step")
        if self.route_kind != "plan_revise" and (self.revision_old_argument is not None
                or self.revision_new_argument is not None):
            raise PydanticCustomError("primary_route_unexpected_revision_arguments", "primary_route_unexpected_revision_arguments")
        if self.route_kind == "ordinary_tool" and self.effect_intent == "mutation_requested":
            permitted = {"create_task": {"title"}, "save_memory": {"key", "value"}}
            if (self.write_tool_name not in permitted
                    or not isinstance(self.write_arguments, dict)
                    or set(self.write_arguments) != permitted[self.write_tool_name]):
                raise PydanticCustomError("primary_route_write_contract_missing", "primary_route_write_contract_missing")
        elif self.write_tool_name is not None or self.write_arguments is not None:
            raise PydanticCustomError("primary_route_unexpected_write_contract", "primary_route_unexpected_write_contract")
        if self.pending_write_tool_name is not None and (
                self.route_kind != "clarify" or self.pending_write_tool_name != "create_task"):
            raise PydanticCustomError("primary_route_pending_write_invalid", "primary_route_pending_write_invalid")
        if (self.route_kind == "clarify") != (self.clarification_question is not None):
            raise PydanticCustomError("primary_route_clarification_mismatch", "primary_route_clarification_mismatch")
        return self


class BoundStep(StrictRouteModel):
    goal: str
    tool_name: Literal["calculate", "mcp_reference_lookup"]
    arguments: dict
    evidence: EvidenceSpan


class BoundPrimaryProposal(StrictRouteModel):
    schema_version: Literal["primary-route-v1"]
    route_kind: RouteKind
    execution_scope: Scope
    effect_intent: Effect
    evidence: EvidenceSpan
    steps: list[BoundStep] = Field(default_factory=list)
    step_number: int | None = None
    clarification_question: str | None = None
    revision_old_argument: str | None = None
    revision_new_argument: str | None = None
    write_tool_name: Literal["create_task", "save_memory"] | None = None
    write_arguments: dict | None = None
    pending_write_tool_name: Literal["create_task"] | None = None


def adapt_legacy_route(value: str) -> RouteKind:
    """Explicit migration adapter; never treats 'other' as an executable route."""
    if value not in LEGACY_ROUTE_ADAPTER:
        raise PrimaryRouteRejected("primary_route_legacy_unmapped")
    return LEGACY_ROUTE_ADAPTER[value]


def bind_unique_quote(message: str, quote: str) -> EvidenceSpan:
    first = message.find(quote)
    if first < 0:
        raise PrimaryRouteRejected("primary_route_quote_not_found")
    if message.find(quote, first + 1) != -1:
        raise PrimaryRouteRejected("primary_route_quote_ambiguous")
    return EvidenceSpan(start=first, end=first + len(quote), exact_text=quote)


def bind_route_signal(signal: RouteSignal, message: str, *,
                      saved_plan_present: bool,
                      pending_action: str | None,
                      pending_origin_verified: bool = False,
                      saved_step_count: int | None = None) -> BoundPrimaryProposal:
    """Host-owned evidence and context checks; DOES NOT authorize execution.

    A pending reply requires a verified durable original request. A saved-plan
    operation requires an existing Host checkpoint; a model may not invent one.
    """
    root = bind_unique_quote(message, signal.quote)
    saved_routes = {"plan_revise", "plan_execute_first", "plan_resume", "plan_cancel"}
    if signal.route_kind in saved_routes and not saved_plan_present:
        raise PrimaryRouteRejected("primary_route_missing_saved_plan")
    if signal.route_kind == "plan_create" and saved_plan_present:
        raise PrimaryRouteRejected("primary_route_existing_saved_plan")
    if signal.route_kind == "plan_missing" and saved_plan_present:
        raise PrimaryRouteRejected("primary_route_plan_exists")
    if signal.route_kind == "clarify_response" and not (
            pending_action and pending_origin_verified):
        raise PrimaryRouteRejected("primary_route_untrusted_pending_origin")
    if signal.route_kind == "plan_revise":
        if saved_step_count is None or signal.step_number > saved_step_count:
            raise PrimaryRouteRejected("primary_route_unbound_step")
        # A revision must bind both the old and replacement arguments to the
        # current message. The existing Host apply_plan_revision validates the
        # checkpoint, explicit step target and exact current stored argument.
        if not (signal.revision_old_argument and signal.revision_new_argument):
            raise PrimaryRouteRejected("primary_route_revision_arguments_missing")
        if signal.revision_old_argument.casefold() not in message.casefold() or (
                signal.revision_new_argument.casefold() not in message.casefold()):
            raise PrimaryRouteRejected("primary_route_revision_arguments_unbound")
    if signal.route_kind == "ordinary_tool" and signal.effect_intent == "mutation_requested":
        from agent.tools import SCHEMAS
        try:
            checked = SCHEMAS[signal.write_tool_name].model_validate(
                signal.write_arguments).model_dump()
        except (ValueError, TypeError) as exc:
            raise PrimaryRouteRejected("primary_route_write_arguments_invalid") from exc
        if any(value.casefold() not in signal.quote.casefold()
               or value.casefold() not in message.casefold()
               for value in checked.values()):
            raise PrimaryRouteRejected("primary_route_write_arguments_unbound")
        # Evidence must contain more than the proposed values alone.
        if signal.quote.strip().casefold() in {
                value.strip().casefold() for value in checked.values()}:
            raise PrimaryRouteRejected("primary_route_write_action_unbound")
    if signal.route_kind == "plan_create":
        bound_steps = []
        for step in signal.steps:
            evidence = bind_unique_quote(message, step.quote)
            names = {"expression"} if step.tool_name == "calculate" else {"term"}
            if set(step.arguments) != names:
                raise PrimaryRouteRejected("primary_route_invalid_step_args")
            value = step.arguments[next(iter(names))]
            if not isinstance(value, str) or not value.strip() or (
                    value.casefold() not in step.quote.casefold()):
                raise PrimaryRouteRejected("primary_route_unbound_step_args")
            bound_steps.append(BoundStep(
                goal=step.goal, tool_name=step.tool_name,
                arguments=step.arguments, evidence=evidence))
    else:
        bound_steps = []
    return BoundPrimaryProposal(
        schema_version=ROUTE_SCHEMA_VERSION, route_kind=signal.route_kind,
        execution_scope=signal.execution_scope, effect_intent=signal.effect_intent,
        evidence=root, steps=bound_steps, step_number=signal.step_number,
        clarification_question=signal.clarification_question,
        revision_old_argument=signal.revision_old_argument,
        revision_new_argument=signal.revision_new_argument,
        write_tool_name=signal.write_tool_name,
        write_arguments=signal.write_arguments,
        pending_write_tool_name=signal.pending_write_tool_name)


_PRIMARY_SYSTEM = (
    "You are the SINGLE semantic route proposer for one current user Turn. "
    "Return EXACTLY one JSON object matching primary-route-v1 schema. "
    "Choose the user's CURRENT intention, not permissions or actual tool names. "
    "plan_create: explicitly save 2-8 ordered executable steps for later, no execution now. "
    "plan_execute_first: explicitly execute ONLY saved plan's first step. "
    "plan_resume: explicitly run remaining saved steps; use remaining_steps scope; "
    "never infer all_steps from a vague request. "
    "plan_revise: explicitly edit one saved step; include a step_number. "
    "For plan_revise, revision_old_argument and revision_new_argument MUST "
    "contain the exact bare tool argument, not a description or a step goal: "
    "for example, if the user says '由计算 5*6 改为计算 7*8', output "
    "revision_old_argument='5*6' and revision_new_argument='7*8'; "
    "NEVER output '计算 5*6', '计算 7*8', '由计算 5*6', or the entire sentence. "
    "The old arithmetic expression must match the current saved step's "
    "stored expected_arguments.expression character-for-character; the "
    "new expression must appear verbatim in current_request. "
    "If user wording does not ground both expressions exactly, use a "
    "clarify route with a question; never invent an expression. "
    "Never invent new arguments or authorize the change. "
    "plan_cancel: explicitly cancel saved plan; no tool execution. "
    "plan_missing: user refers to executing or continuing a previously saved "
    "plan, but trusted saved_plan_exists=false. "
    "The saved_plan_exists field is a Host-supplied snapshot fact, not a user "
    "claim. If saved_plan_exists=false AND current_request asks to resume a "
    "previously saved plan, choose plan_missing (not plan_resume). "
    "Likewise, asking to run the first step of a nonexistent saved plan "
    "requires plan_missing (not plan_execute_first). "
    "Do not invent a saved plan from recent_context or a user's claim. "
    "If saved_plan_exists=true, use plan_resume or plan_execute_first for "
    "their respective explicit requests; do not choose plan_missing. "
    "For plan_missing set execution_scope=none, effect_intent=none, "
    "steps=[], step_number=null and use an exact unique quote of the "
    "current request. Host verifies snapshot existence again before "
    "any decision; the model's choice never grants authority. "
    "ordinary_tool: independent tool work, including calculations and references "
    "even with saved plan present. For a create_task WRITE, set "
    "effect_intent=mutation_requested, write_tool_name=create_task and "
    "write_arguments with a verbatim title. For a memory WRITE set "
    "write_tool_name=save_memory and write_arguments with exact key/value. "
    "Every write argument must occur within a unique current-request quote. "
    "Host independently requires current-turn consent for memory writes. "
    "For read-only tools set write_tool_name/write_arguments null. "
    "Never invent titles, memory values, or write authorization. "
    "respond: explanation only; clarify: request is ambiguous or missing a required "
    "argument, include clarification_question; clarify_response: reply to a "
    "verified pending question; reject: no supported route. "
    "If the CURRENT user explicitly asks to create_task but omits a title, "
    "choose clarify, include clarification_question, set "
    "pending_write_tool_name=create_task, and use a verbatim source quote "
    "grounding the ORIGINAL task-creation request. Never set this field "
    "for ordinary questions or existing pending responses. "
    "Always copy a UNIQUE contiguous verbatim quote from current_request as evidence. "
    "Never use legacy alias 'other'. If unsure, clarify/reject, not an executable route. "
    "For plan_create output ordered steps with exact quote and grounded arguments. "
    "OUTPUT-vs-CONTEXT BOUNDARY: saved_plan_steps in the input JSON is a "
    "READ-ONLY Host snapshot used to understand an existing plan. NEVER "
    "copy saved_plan_steps or any of its entries into the output field steps. "
    "The output steps field is ONLY a proposed NEW plan definition and is "
    "allowed nonempty ONLY for route_kind=plan_create. For plan_execute_first, "
    "plan_resume, plan_revise, plan_cancel, plan_missing, ordinary_tool, "
    "respond, clarify, clarify_response and reject, output steps=[] EXACTLY, "
    "even when saved_plan_exists=true and saved_plan_steps is nonempty. "
    "For FIRST or RESUME do not re-list or re-create existing steps; Host "
    "loads and executes eligible checkpoint steps only after admission. "
    "step_number and revision_old_argument and revision_new_argument only "
    "for plan_revise. "
    "Set scope/effect as follows: create/revise/cancel none+mutation_requested, "
    "execute_first first_step+read_only, resume remaining_steps+read_only, "
    "ordinary_tool none+read_only or mutation_requested, "
    "all other routes none+none. Do not infer Host rights or execute anything. "
    "CRITICAL OUTPUT CONTRACT: execution_scope is a bound on SAVED PLAN steps, "
    "NOT a tool-use flag. For an independent calculation or reference query "
    "use route_kind=ordinary_tool, execution_scope=none, effect_intent=read_only. "
    "Do NOT use execution_scope=single_tool for ordinary_tool. "
    "For explanations use respond+none+none, never respond+read_only. "
    "For a missing saved plan use plan_missing+none+none. "
    "The only valid (route_kind, execution_scope, effect_intent) combinations are: "
    "plan_create/none/mutation_requested; plan_revise/none/mutation_requested; "
    "plan_cancel/none/mutation_requested; plan_execute_first/first_step/read_only; "
    "plan_resume/remaining_steps/read_only; ordinary_tool/none/read_only; "
    "ordinary_tool/none/mutation_requested (explicit create_task or save_memory only); "
    "plan_missing/none/none; respond/none/none; clarify/none/none; "
    "clarify_response/none/none; reject/none/none. "
    "Before returning JSON, check the triple exactly against this table. "
    "For ordinary_tool read-only, do not include write fields or a step_number. "
    "Use ALL schema keys with JSON null for unused optional fields. "
    "CONTRASTIVE CONTEXT EXAMPLES (classification only, not execution): "
    "saved_plan_exists=false + '继续执行之前保存的计划剩余步骤' -> "
    "plan_missing/none/none; saved_plan_exists=true + the same request -> "
    "plan_resume/remaining_steps/read_only. "
    "saved_plan_exists=false + '请创建两步计划：第一步算3+4，第二步算8+9，只保存不执行' "
    "-> plan_create/none/mutation_requested. "
    "No keyword rules: use the user's meaning and the Host snapshot fact. "
    "Model guesses never authorize execution. "
)

# The example is rendered as valid JSON rather than handwritten escaped text.
# It is a format example only; the model must bind the quote to the request.
_PRIMARY_SYSTEM += (
    " Read-only tool OUTPUT FORMAT EXAMPLE ONLY (not a default route): "
    + json.dumps({
        "schema_version": ROUTE_SCHEMA_VERSION,
        "route_kind": "ordinary_tool",
        "quote": "a unique verbatim phrase from current_request",
        "execution_scope": "none",
        "effect_intent": "read_only",
        "steps": [],
        "step_number": None,
        "clarification_question": None,
        "revision_old_argument": None,
        "revision_new_argument": None,
        "write_tool_name": None,
        "write_arguments": None,
        "pending_write_tool_name": None,
    }, ensure_ascii=False)
    + " Never copy the example quote; copy a unique exact substring of the request."
)

# Explicit existing-plan example uses steps=[] even when the INPUT has a plan.
# A model must propose a control intent, never echo the stored checkpoint.
_PRIMARY_SYSTEM += (
    " Existing-plan FIRST OUTPUT FORMAT EXAMPLE ONLY: "
    + json.dumps({
        "schema_version": ROUTE_SCHEMA_VERSION,
        "route_kind": "plan_execute_first",
        "quote": "只执行已保存计划的第一步",
        "execution_scope": "first_step",
        "effect_intent": "read_only",
        "steps": [],
        "step_number": None,
        "clarification_question": None,
        "revision_old_argument": None,
        "revision_new_argument": None,
        "write_tool_name": None,
        "write_arguments": None,
        "pending_write_tool_name": None,
    }, ensure_ascii=False)
    + " If current_request asks to run just the first saved step and "
    "saved_plan_exists=true, this example shows the output field structure "
    "only; replace quote with an exact unique substring of current_request. "
    "For RESUME similarly use plan_resume/remaining_steps/read_only and "
    "steps=[] regardless of the number of saved steps."
)


@attributed("primary")
async def propose_primary_route(provider, model: str, message: str, *,
                                saved_plan_present: bool,
                                pending_action: str | None = None,
                                pending_origin_verified: bool = False,
                                saved_step_count: int | None = None,
                                saved_plan_steps: list[dict] | None = None,
                                recent_context: list | None = None
                                ) -> BoundPrimaryProposal:
    """Exactly one model selection invocation; no legacy semantic fallback.

    Host may deny or clarify after this function, but cannot silently change
    the route. A provider/schema/binding failure does not invoke another owner.
    """
    if not message.strip():
        raise PrimaryRouteRejected("primary_route_empty_message")
    payload = {
        "current_request": message, "saved_plan_exists": saved_plan_present,
        "saved_plan_steps": (saved_plan_steps or [])[:8] if saved_plan_present else [],
        "pending_action": pending_action if pending_origin_verified else None,
        "recent_context": (recent_context or [])[-6:],
    }
    raw = await provider.complete(
        model=model,
        messages=[{"role": "system", "content": _PRIMARY_SYSTEM},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        response_schema=RouteSignal.model_json_schema())
    try:
        signal = RouteSignal.model_validate_json(raw)
    except ValidationError as exc:
        issues = safe_route_validation_issues(exc)
        raise PrimaryRouteRejected(
            "primary_route_invalid_signal",
            validation_issues=issues,
            route_triple=safe_route_triple(raw, issues)) from exc
    except ValueError as exc:
        raise PrimaryRouteRejected("primary_route_invalid_signal") from exc
    return bind_route_signal(
        signal, message, saved_plan_present=saved_plan_present,
        pending_action=pending_action, pending_origin_verified=pending_origin_verified,
        saved_step_count=saved_step_count)
