"""Opt-in shared saved-plan semantic route owner for M5 integration.

The model chooses an operation; exact evidence/captured Turn is bound by Host.
This selector is not an execution or mutation authorization.
"""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class SavedPlanRouteRefused(ValueError):
    """Model named a saved-plan operation but failed deterministic source binding."""


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
        "Return JSON route, quote, start, end. The quote MUST be a contiguous "
        "EXACT substring of current message proving route. start/end are "
        "zero-based Unicode codepoint offsets, end exclusive. For other, quote "
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
    if message[signal.start:signal.end] != signal.quote:
        raise SavedPlanRouteRefused("m5_unified_source_mismatch")
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
    op["evidence"] = [{"start": signal.start, "end": signal.end,
                        "exact_text": signal.quote}]
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
