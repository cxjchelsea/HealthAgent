"""Opt-in shared saved-plan semantic route owner for M5 integration.

The model chooses an operation; exact evidence/captured Turn is bound by Host.
This selector is not an execution or mutation authorization.
"""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.plan_intent import PlanRequest
from agent.route_primary_m5_binding import bind_primary_proposal

from agent.m5_model_call_metrics import attributed
from agent.route_primary_m5 import PrimarySemanticProposal
from agent.route_primary_m5_capture import bind_captured_proposal
from agent.route_primary_m5_binding import ProposalBindingRefused
from agent.understanding import UnderstandingError


class SavedPlanRouteSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Literal["plan_execute_first", "plan_resume", "plan_revise",
                   "plan_cancel", "other"]
    quote: str = Field(min_length=1, max_length=400)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)


class SavedPlanRouteRefused(ValueError):
    """Model named a saved-plan operation but failed deterministic source binding."""

def _host_unique_span(message: str, quote: str) -> dict:
    """Only exact, uniquely occurring quote text can supply source evidence.

    Model-provided numerical offsets are advisory and never grant authority.
    """
    start = message.find(quote)
    if start < 0:
        raise SavedPlanRouteRefused("m5_quote_not_found")
    if message.find(quote, start + 1) >= 0:
        raise SavedPlanRouteRefused("m5_quote_ambiguous")
    return {"start": start, "end": start + len(quote), "exact_text": quote}


@attributed("primary")
async def propose_saved_plan_route(provider, model: str, message: str, capture, *,
                                  conversation_id: str, request_id: str,
                                  turn_id: str, checkpoint: dict) -> str:
    """Select exactly one route. Raises on invalid/unsafe evidence; never runs tools."""
    prompt = (
        "Select ONE route for CURRENT user request about an ALREADY SAVED plan: "
        "plan_execute_first ONLY for explicitly running its first step, "
        "plan_resume ONLY for explicitly running remaining saved steps, "
        "plan_revise ONLY for editing a saved step without execution, "
        "plan_cancel ONLY for cancelling the whole saved plan without execution, "
        "otherwise other (ordinary chat, tool commands, new plan, ambiguous). "
        "Return JSON route and quote; omit start/end. The quote MUST be a contiguous "
        "EXACT substring of current message proving route. It must be "
        "copied character-for-character and occur exactly once. For other, quote "
        "an exact nonempty substring too. Never infer permissions or execute."
    )
    raw = await provider.complete(
        model=model, messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(
                {"current_request": message, "saved_plan_exists": True},
                ensure_ascii=False)}],
        response_schema=SavedPlanRouteSignal.model_json_schema())
    try:
        signal = SavedPlanRouteSignal.model_validate_json(raw)
    except ValueError as exc:
        raise UnderstandingError("m5_unified_primary_invalid") from exc
    source_span = _host_unique_span(message, signal.quote)
    if signal.route == "other":
        return "other"
    operations = {
        "plan_execute_first": {
            "route": "plan_execute_first", "scope": "first_step"},
        "plan_resume": {
            "route": "plan_resume", "scope": "remaining_steps"},
        "plan_revise": {
            "route": "plan_revise", "step_id": 1,
            "expected_old_arguments": {}, "proposed_arguments": {}},
        "plan_cancel": {"route": "plan_cancel"},
    }
    op = dict(operations[signal.route])
    op["evidence"] = [source_span]
    scope = ("first_step" if signal.route == "plan_execute_first"
             else "remaining_steps" if signal.route == "plan_resume" else "none")
    try:
        proposal = PrimarySemanticProposal.model_validate({
            "schema_version": "s25-m5-primary-v1",
            "route": signal.route,
            "scope": scope,
            "effect_intent": ("read_only" if signal.route in {
                "plan_execute_first", "plan_resume"} else "mutation_requested"),
            "ambiguity": "clear", "reference": {"kind": "saved_plan"},
            "operation": op,
        })
        bind_captured_proposal(
            proposal, message, capture, conversation_id=conversation_id,
            request_id=request_id, turn_id=turn_id, checkpoint=checkpoint)
    except (ValueError, TypeError, ProposalBindingRefused) as exc:
        raise SavedPlanRouteRefused("m5_unified_binding_refused") from exc
    return signal.route


class ProposedNewStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=400)
    tool_name: Literal["calculate", "mcp_reference_lookup"]
    arguments: dict
    quote: str = Field(min_length=1, max_length=400)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)


class NewPlanRouteSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Literal["plan_create", "other"]
    quote: str = Field(min_length=1, max_length=400)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    steps: list[ProposedNewStep] = Field(default_factory=list, max_length=8)


@attributed("primary")
async def propose_new_plan_route(provider, model: str, message: str) -> PlanRequest:
    """A single route decision for no-saved-plan turns; Host still owns CAS.

    An invalid CREATE proposal fails closed rather than falling back to the
    old intent classifier or ordinary AgentLoop tool execution.
    """
    prompt = (
        "Decide current user's ONE route: plan_create ONLY if explicitly asking "
        "to CREATE/SAVE a FUTURE EXECUTABLE plan without running any step now; "
        "otherwise other (standalone tools, chat, follow-ups, ambiguous or "
        "requests to execute a missing prior plan). "
        "Return JSON route, quote, steps (omit start/end). For plan_create, output "
        "2 to 8 ordered steps ONLY, with goal, tool_name (calculate or "
        "mcp_reference_lookup), arguments (calculate: expression; lookup: term), "
        "quote as an EXACT user substring containing each argument. "
        "The top-level quote must exactly prove the user's plan-save request. "
        "Each quote must occur exactly once in the current user request. "
        "Never invent arguments, execute tools, or infer authority. "
        "For other return steps=[] and an exact nonempty user substring."
    )
    raw = await provider.complete(
        model=model, messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(
                {"current_request": message, "saved_plan_exists": False},
                ensure_ascii=False)}],
        response_schema=NewPlanRouteSignal.model_json_schema())
    try:
        signal = NewPlanRouteSignal.model_validate_json(raw)
    except ValueError as exc:
        raise UnderstandingError("m5_unified_create_invalid") from exc
    source_span = _host_unique_span(message, signal.quote)
    if signal.route == "other":
        if signal.steps:
            raise SavedPlanRouteRefused("m5_create_unexpected_steps")
        return PlanRequest(intent="other", steps=[])
    if not 2 <= len(signal.steps) <= 8:
        raise SavedPlanRouteRefused("m5_create_invalid_step_count")
    proposal_steps = []
    plan_steps = []
    for step in signal.steps:
        step_span = _host_unique_span(message, step.quote)
        if set(step.arguments) != ({"expression"} if step.tool_name == "calculate"
                                    else {"term"}):
            raise SavedPlanRouteRefused("m5_create_invalid_arguments")
        value = next(iter(step.arguments.values()))
        if not isinstance(value, str) or value.casefold() not in step.quote.casefold():
            raise SavedPlanRouteRefused("m5_create_argument_not_in_evidence")
        proposal_steps.append({
            "goal": step.goal, "tool_name": step.tool_name,
            "arguments": step.arguments,
            "evidence": [step_span],
        })
        plan_steps.append({
            "goal": step.goal, "expected_tool": step.tool_name,
            "expected_arguments": step.arguments,
        })
    try:
        proposal = PrimarySemanticProposal.model_validate({
            "schema_version": "s25-m5-primary-v1",
            "route": "plan_create", "scope": "none",
            "effect_intent": "mutation_requested", "ambiguity": "clear",
            "reference": {"kind": "none"},
            "evidence": [source_span],
            "operation": {"route": "plan_create", "save_only": True,
                          "steps": proposal_steps},
        })
        bind_primary_proposal(proposal, message)
    except (ValueError, TypeError, ProposalBindingRefused) as exc:
        raise SavedPlanRouteRefused("m5_create_binding_refused") from exc
    return PlanRequest(intent="plan_only", steps=plan_steps)
