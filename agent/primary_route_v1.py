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
    "write_arguments", "pending_write_tool_name", "pending_read_tool_name", "goal", "tool_name",
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
    "primary_route_pending_read_invalid",
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
    pending_read_tool_name: Literal["mcp_reference_lookup"] | None = None

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
        if self.pending_read_tool_name is not None and (
                self.route_kind != "clarify"
                or self.pending_read_tool_name != "mcp_reference_lookup"
                or self.pending_write_tool_name is not None):
            raise PydanticCustomError("primary_route_pending_read_invalid", "primary_route_pending_read_invalid")
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
    pending_read_tool_name: Literal["mcp_reference_lookup"] | None = None


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
        pending_write_tool_name=signal.pending_write_tool_name,
        pending_read_tool_name=signal.pending_read_tool_name)


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
    "When user asks for an MCP reference lookup but has not given a term, "
    "propose clarify with pending_read_tool_name=mcp_reference_lookup and "
    "ask explicitly for the term. This does not authorize a tool call. "
    "For a NEW MCP lookup that is missing its search term, return "
    "route_kind=clarify, execution_scope=none, effect_intent=none, "
    "clarification_question as a nonempty question asking for the term, "
    "pending_read_tool_name=mcp_reference_lookup, "
    "pending_write_tool_name=null, write_tool_name=null, "
    "write_arguments=null, steps=[], step_number=null. "
    "Never combine pending_read_tool_name with pending_write_tool_name. "
    "The pending_read_tool_name is ONLY a clarification marker, "
    "never an execution permission; for ordinary_tool, respond, "
    "clarify_response and every other route it MUST be null. "
    "The same exclusivity applies in reverse: a NEW missing-title "
    "create_task clarification sets pending_write_tool_name=create_task "
    "and pending_read_tool_name=null. "
    "When pending_action comes from verified Host state and equals create_task "
    "or mcp_reference_lookup, a reply supplying the missing title or term "
    "MUST be clarify_response with scope=none effect=none and steps=[]. "
    "Never choose clarify again for a valid answer to the pending question. "
    "The quote must uniquely match the CURRENT reply. "
    "PENDING CONTEXT RULE: If pending_origin_verified=true and "
    "pending_action is create_task or mcp_reference_lookup, use the trusted "
    "pending_question as context for interpreting current_request. A short "
    "standalone answer that supplies the requested title or search term is "
    "clarify_response/none/none, even when it lacks a verb such as create or "
    "search. A reply that explicitly cancels, changes the subject, or still "
    "omits the requested value is not an executable clarification response. "
    "Pending write cancellation is a DIFFERENT intent from supplying a title. "
    "If current_request withdraws, declines, or says not to proceed with "
    "the outstanding task creation, choose respond/none/none, not "
    "clarify_response, even when trusted pending_action=create_task. "
    "A negative instruction is not the missing title and must never be "
    "copied as a new task title. Likewise an unrelated question is "
    "a new request, not a pending answer. For these non-resumption routes "
    "keep write_tool_name, write_arguments, pending_write_tool_name and "
    "pending_read_tool_name null, and steps=[]. "

    "For clarify_response, clarification_question=null, pending_write_tool_name=null, "
    "pending_read_tool_name=null, write_tool_name=null, write_arguments=null, "
    "steps=[], step_number=null; output only a quote anchored to CURRENT reply. "
    "A pending_read_tool_name or pending_write_tool_name is permitted only "
    "when first asking a NEW clarifying question, NEVER in clarify_response. "
    "If the Host says pending_action=null, do not infer pending authority "
    "from recent_context. "
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

# Pending is context, NOT an instruction to force continuation. Keep this
# contrastive priority above output examples so an example clarify_response
# does not silently become the default for every Pending Turn.
_PRIMARY_SYSTEM += (
    " PENDING CURRENT-TURN PRIORITY (apply before choosing route_kind): "
    "Interpret current_request as a NEW autonomous user message first. "
    "The pending_question is historical context, not a command to fill its slot. "
    "First decide whether the USER is continuing that exact missing-value "
    "request or issuing another conversational request. "
    "Only a reply that supplies the requested parameter AS ITS PURPOSE may "
    "choose clarify_response. A sentence that instructs the assistant to "
    "explain, summarize, compare, answer, find information, or do independent "
    "work is a fresh request, even when a Pending task-title question exists. "
    "Do not store such a sentence as a task title or assume that any "
    "nonempty text is a title. A direct standalone title can be a noun "
    "phrase or an imperative describing future work, but the user is not "
    "asking for that work to be performed immediately in the current Turn. "
    "When the current message requests a new answer, use respond; when it "
    "requests an independent available tool, use ordinary_tool; when it refers "
    "to a nonexistent saved plan use plan_missing; when it declines the "
    "pending write, use respond. Always judge current intent in its full "
    "conversational context rather than matching literal trigger words. "
    "CONTRAST A: prior question asks for a task title, current reply is "
    "'核验样机编号' => clarify_response, the requested parameter itself. "
    "CONTRAST B: same prior question, current reply is "
    "'现在请介绍一下什么是远程过程调用' => respond, an independent "
    "information request, NOT a title. "
    "CONTRAST C: same prior question, current reply is "
    "'请计算 17+24' => ordinary_tool/read_only, a new immediate "
    "calculation rather than a title for later. "
    "CONTRAST D: same prior question, current reply is "
    "'整理不再续订服务的记录' => clarify_response, this is a possible "
    "standalone title even though its subject mentions stopping a service. "
    "CONTRAST E: same prior question, current reply is "
    "'我不想创建这项待办了' => respond, a withdrawal. "
    "These examples are semantic contrasts, never outputs to copy. "
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
        "pending_read_tool_name": None,
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
        "pending_read_tool_name": None,
    }, ensure_ascii=False)
    + " If current_request asks to run just the first saved step and "
    "saved_plan_exists=true, this example shows the output field structure "
    "only; replace quote with an exact unique substring of current_request. "
    "For RESUME similarly use plan_resume/remaining_steps/read_only and "
    "steps=[] regardless of the number of saved steps."
)

# Do not put a full clarify_response JSON example at the end of the global
# system instruction: it can anchor a small model to choose continuation
# whenever a Pending origin is present, even for a fresh independent request.


_PENDING_CURRENT_TURN_SYSTEM = (
    "PENDING CURRENT-TURN SEMANTIC DECISION (single route proposal, no new "
    "model call, no keyword classifier): "
    "The Host's pending_action and pending_question describe the PREVIOUS "
    "assistant's unresolved task, not the user's CURRENT goal. "
    "Before choosing route_kind, interpret current_request on its own: "
    "What is the user asking the assistant to accomplish RIGHT NOW? "
    "A fresh request for an explanation, comparison, answer, calculation, "
    "lookup, or other immediate action keeps its own route even if it is "
    "a short Chinese phrase without punctuation or a subject-change phrase. "
    "For a fresh informational answer use respond/none/none; for an "
    "independent available read-only tool use ordinary_tool/none/read_only. "
    "For a withdrawal use respond/none/none. "
    "Only when the CURRENT user message is supplying a missing field of "
    "the prior pending request, rather than asking the assistant to do "
    "new work now, choose clarify_response/none/none. "
    "A task title is typically a proposed label for FUTURE work. "
    "A message that asks YOU to perform work NOW is not automatically a title. "
    "Do not determine this from one imperative verb, punctuation, length, "
    "or the mere existence of pending_action; decide by conversational "
    "purpose. A legitimate title may itself be phrased as an imperative. "
    "Counterfactual check: if pending_question were absent, would this "
    "message still be an actionable new user request to answer or execute? "
    "If yes, keep that independent intent. If it would merely supply the "
    "missing task title, it is clarify_response. "
    "Examples illustrating the distinction, not exact-match rules: "
    "pending title + '核对检测设备编号' -> clarify_response; "
    "pending title + '请说明向量索引的适用场景' -> respond; "
    "pending title + '请说明事件驱动架构的作用' -> respond; "
    "pending title + '整理不再续订服务的记录' -> clarify_response; "
    "pending title + '这条待办先不创建了' -> respond. "
    "If user intent truly cannot be distinguished, choose clarify and "
    "ask a neutral question rather than silently treating unrelated "
    "text as a task-title answer. "
    "All output fields must obey the same primary-route-v1 schema. "
    "Do not propose a new write or reuse the original Pending authorization."
)


_PENDING_MCP_CURRENT_TURN_SYSTEM = (
    "VERIFIED PENDING READ-ONLY LOOKUP: the previous user asked the assistant "
    "to look up a technical term; the assistant already asked which term. "
    "pending_action=mcp_reference_lookup and pending_origin_verified=true "
    "are Host-provided context for interpretation, not new tool permission. "
    "Interpret the current_request in relation to pending_question BEFORE "
    "choosing route_kind. If it provides a usable search term requested by "
    "the previous clarification, the term is the complete missing value: "
    "choose clarify_response/none/none, clarification_question=null, "
    "pending_read_tool_name=null, pending_write_tool_name=null, "
    "write_tool_name=null, write_arguments=null and steps=[]. "
    "A short acronym, single technical noun, or phrase can be a complete "
    "search term. Do not ask for an expanded definition or another term "
    "merely because it is short. The Host, not the model, authorizes the "
    "read-only lookup and binds the origin. "
    "If the user instead asks to cancel the lookup or asks an independent "
    "question, choose respond or the appropriate independent read-only route; "
    "do not silently reuse pending authority. If the reply really still "
    "omits the requested search term, choose clarify with a new question. "
    "Semantic contrasts (not literal trigger matches): "
    "asked for a term + 'API 网关' -> clarify_response; "
    "asked for a term + '向量检索' -> clarify_response; "
    "asked for a term + '我不查了' -> respond; "
    "asked for a term + '先给我介绍一下分布式事务' -> respond. "
    "Always use a unique verbatim quote from current_request, "
    "never copy the pending_question as evidence. "
    "Do not default to clarify simply because a prior question is present. "
    "Output one valid primary-route-v1 proposal, not an execution result."
)


def primary_route_generation_schema(message: str, *,
                                    pending_origin_verified: bool = False) -> dict:
    """Constrain generation, never rewrite or authorize a model proposal.

    For a short request the whole request is already a unique source span.
    The provider must copy it exactly; the existing Host binding still
    checks it. Verified Pending contexts forbid reusing the origin's
    missing-tool marker or a new write contract as output permission. Do not preselect
    route_kind: a user can withdraw or change a Pending request.
    """
    schema = RouteSignal.model_json_schema()
    if 1 <= len(message) <= 400:
        schema["properties"]["quote"] = {
            "type": "string", "const": message,
            "minLength": 1, "maxLength": 400,
            "description": "Exact complete current_request; source evidence, not authority",
        }
    if pending_origin_verified:
        for field in ("pending_write_tool_name", "pending_read_tool_name",
                      "write_tool_name", "write_arguments"):
            schema["properties"][field] = {
                "type": "null",
                "description": "Verified Pending origin belongs to Host; this reply cannot propose new write or pending authority",
            }
    return schema


@attributed("primary")
async def propose_primary_route(provider, model: str, message: str, *,
                                saved_plan_present: bool,
                                pending_action: str | None = None,
                                pending_origin_verified: bool = False,
                                pending_question: str | None = None,
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
        "pending_origin_verified": bool(pending_action and pending_origin_verified),
        "pending_question": (pending_question[:500] if pending_question and pending_origin_verified else None),
        "recent_context": (recent_context or [])[-6:],
    }
    # A separate trailing system instruction is used ONLY for a verified
    # Pending context. It is adjacent to the current request in the model
    # context; it does not add a second model invocation or post-hoc route edit.
    messages = [{"role": "system", "content": _PRIMARY_SYSTEM}]
    if pending_origin_verified and pending_action == "create_task":
        messages.append({"role": "system",
                         "content": _PENDING_CURRENT_TURN_SYSTEM})
    elif pending_origin_verified and pending_action == "mcp_reference_lookup":
        messages.append({"role": "system",
                         "content": _PENDING_MCP_CURRENT_TURN_SYSTEM})
    messages.append({"role": "user",
                     "content": json.dumps(payload, ensure_ascii=False)})
    raw = await provider.complete(
        model=model,
        messages=messages,
        response_schema=primary_route_generation_schema(
            message, pending_origin_verified=bool(
                pending_action and pending_origin_verified)))
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
