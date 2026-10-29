"""M5 experimental primary proposal for a saved-plan FIRST request.

No tool invocation and no fallback to a second intent classifier.
"""
import json
from pydantic import BaseModel, ConfigDict, Field
from agent.route_primary_m5 import PrimarySemanticProposal
from agent.route_primary_m5_capture import bind_captured_proposal
from agent.route_primary_m5_binding import ProposalBindingRefused
from agent.understanding import UnderstandingError
from agent.m5_model_call_metrics import attributed


class FirstRouteSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_step_requested: bool
    quote: str = Field(min_length=1, max_length=400)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


@attributed("primary")
async def propose_first_route(provider, model: str, message: str, capture,
                              *, conversation_id: str, request_id: str,
                              turn_id: str, checkpoint: dict | None) -> bool:
    """True means only that semantic evidence binds; M4 still authorizes execution."""
    prompt = (
        "Decide whether the CURRENT user explicitly asks to execute ONLY the "
        "FIRST step of an ALREADY SAVED plan. Return JSON: first_step_requested "
        "(boolean), quote (exact substring of current request), start and end "
        "(zero-based Unicode codepoint offsets, end exclusive). If not explicitly "
        "first-step execution, answer false. Do not infer permissions, execute "
        "tools, use history as a new request, or treat edits as execution. "
        "The quote must support the decision."
    )
    raw = await provider.complete(
        model=model, messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(
                {"current_request": message, "has_saved_plan": checkpoint is not None},
                ensure_ascii=False)}],
        response_schema=FirstRouteSignal.model_json_schema())
    try:
        signal = FirstRouteSignal.model_validate_json(raw)
    except ValueError as exc:
        raise UnderstandingError("m5_primary_invalid") from exc
    if not signal.first_step_requested:
        return False
    proposal = PrimarySemanticProposal.model_validate({
        "schema_version": "s25-m5-primary-v1",
        "route": "plan_execute_first",
        "scope": "first_step",
        "effect_intent": "read_only",
        "ambiguity": "clear",
        "reference": {"kind": "saved_plan"},
        "operation": {
            "route": "plan_execute_first", "scope": "first_step",
            "evidence": [{"start": signal.start, "end": signal.end,
                          "exact_text": signal.quote}],
        },
    })
    try:
        bind_captured_proposal(
            proposal, message, capture,
            conversation_id=conversation_id, request_id=request_id,
            turn_id=turn_id, checkpoint=checkpoint)
    except ProposalBindingRefused:
        return False
    return True
