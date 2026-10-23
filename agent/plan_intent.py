"""Model-classified plan-only request; host validates plan bindings before persistence.

Planning never invokes business tools or creates authorization for later execution.
"""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from agent.planning import PlanLedger
from agent.tools import SCHEMAS
from agent.understanding import HTTPUnderstandingModel, UnderstandingError


READ_TOOLS = {"calculate", "mcp_reference_lookup", "utc_time", "list_tasks", "recall_memory"}


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["plan_only", "execute_first", "resume", "other"]
    steps: list[dict] = Field(default_factory=list, max_length=8)


async def review_plan_intent(provider: HTTPUnderstandingModel, model: str,
                             message: str, existing_plan: bool) -> PlanRequest:
    prompt = (
        "Return ONLY JSON: intent (plan_only|execute_first|resume|other) and steps. "
        "plan_only ONLY when the current user asks to CREATE/DRAFT a future execution "
        "plan without starting it now. For plan_only, list fully bound read-only "
        "steps from the current user's explicit request: each has goal, expected_tool, "
        "expected_arguments. Supported types: calculate(expression), "
        "mcp_reference_lookup(term). Do not invent a missing argument. "
        "execute_first ONLY if user explicitly authorizes running just step one "
        "of an already saved plan. resume ONLY if user explicitly authorizes "
        "continuing remaining steps in a saved plan. Otherwise other. "
        "Never execute tools, infer authorization or claim completion."
    )
    raw = await provider.complete(model=model, messages=[
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(
            {"current_request": message, "existing_plan": existing_plan},
            ensure_ascii=False)}], response_schema=PlanRequest.model_json_schema())
    try:
        return PlanRequest.model_validate_json(raw)
    except ValueError as exc:
        raise UnderstandingError("plan_intent_invalid") from exc


def build_plan_checkpoint(message: str, decision: PlanRequest) -> dict:
    if decision.intent != "plan_only" or not decision.steps:
        raise ValueError("not_a_bound_plan")
    steps = []
    for raw in decision.steps:
        if not isinstance(raw, dict) or set(raw) != {
                "goal", "expected_tool", "expected_arguments"}:
            raise ValueError("invalid_bound_step")
        name, args = raw["expected_tool"], raw["expected_arguments"]
        if name not in {"calculate", "mcp_reference_lookup"} or not isinstance(args, dict):
            raise ValueError("unsafe_plan_step")
        parsed = SCHEMAS[name].model_validate(args).model_dump()
        value = parsed["expression"] if name == "calculate" else parsed["term"]
        if value.casefold() not in message.casefold():
            raise ValueError("ungrounded_plan_step")
        steps.append({"goal": raw["goal"], "expected_tool": name,
                      "expected_arguments": parsed})
    ledger = PlanLedger()
    result = ledger.propose(steps)
    if not result.get("ok"):
        raise ValueError("plan_rejected")
    return ledger.checkpoint()
