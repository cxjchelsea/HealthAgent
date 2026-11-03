"""Primary-route-v1: ONE semantic proposal, not an execution authority.

Work package 1 contract. Host validates source quotes and trusted context;
it never treats the model's declared intent as authorization to mutate state.
This module deliberately has no tool, database, or AgentLoop imports.
"""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.route_shadow import EvidenceSpan
from agent.m5_model_call_metrics import attributed

ROUTE_SCHEMA_VERSION = "primary-route-v1"
RouteKind = Literal[
    "plan_create", "plan_revise", "plan_execute_first", "plan_resume",
    "plan_cancel", "plan_missing", "ordinary_tool", "respond",
    "clarify", "clarify_response", "reject",
]
Scope = Literal["none", "first_step", "remaining_steps", "all_steps", "single_tool"]
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
            raise ValueError("primary_route_scope_effect_mismatch")
        if (self.route_kind == "plan_create" and not 2 <= len(self.steps) <= 8) or (self.route_kind != "plan_create" and self.steps):
            raise ValueError("primary_route_steps_mismatch")
        if self.route_kind == "plan_revise" and self.step_number is None:
            raise ValueError("primary_route_revision_needs_step")
        if self.route_kind != "plan_revise" and self.step_number is not None:
            raise ValueError("primary_route_unexpected_step")
        if self.route_kind != "plan_revise" and (self.revision_old_argument is not None
                or self.revision_new_argument is not None):
            raise ValueError("primary_route_unexpected_revision_arguments")
        if (self.route_kind == "clarify") != (self.clarification_question is not None):
            raise ValueError("primary_route_clarification_mismatch")
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
        revision_new_argument=signal.revision_new_argument)


_PRIMARY_SYSTEM = (
    "You are the SINGLE semantic route proposer for one current user Turn. "
    "Return EXACTLY one JSON object matching primary-route-v1 schema. "
    "Choose the user's CURRENT intention, not permissions or actual tool names. "
    "plan_create: explicitly save 2-8 ordered executable steps for later, no execution now. "
    "plan_execute_first: explicitly execute ONLY saved plan's first step. "
    "plan_resume: explicitly run remaining saved steps; use remaining_steps scope; "
    "never infer all_steps from a vague request. "
    "plan_revise: explicitly edit one saved step; include a step_number but do not "
    "extract old and replacement arguments ONLY when both appear verbatim in "
    "current_request; otherwise leave them null to request clarification. "
    "plan_cancel: explicitly cancel saved plan; no tool execution. "
    "plan_missing: user explicitly refers to executing an absent saved plan; "
    "absence is provisional until Host checks it. "
    "ordinary_tool: independent tool work, including calculations, task/memory "
    "commands or references, even with saved plan present. No concrete tool choice. "
    "respond: explanation only; clarify: request is ambiguous or missing a required "
    "argument, include clarification_question; clarify_response: reply to a "
    "verified pending question; reject: no supported route. "
    "Always copy a UNIQUE contiguous verbatim quote from current_request as evidence. "
    "Never use legacy alias 'other'. If unsure, clarify/reject, not an executable route. "
    "For plan_create output ordered steps with exact quote and grounded arguments. "
    "For non-create routes steps must be empty; step_number and revision_old_argument "
    "and revision_new_argument only for plan_revise. "
    "Set scope/effect as follows: create/revise/cancel none+mutation_requested, "
    "execute_first first_step+read_only, resume remaining_steps+read_only, "
    "ordinary_tool none+read_only or mutation_requested, "
    "all other routes none+none. Do not infer Host rights or execute anything."
)


@attributed("primary")
async def propose_primary_route(provider, model: str, message: str, *,
                                saved_plan_present: bool,
                                pending_action: str | None = None,
                                pending_origin_verified: bool = False,
                                saved_step_count: int | None = None,
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
    except ValueError as exc:
        raise PrimaryRouteRejected("primary_route_invalid_signal") from exc
    return bind_route_signal(
        signal, message, saved_plan_present=saved_plan_present,
        pending_action=pending_action, pending_origin_verified=pending_origin_verified,
        saved_step_count=saved_step_count)
