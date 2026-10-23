"""Host admission for model-proposed saved-plan control.

Classification is not authority. Route a missing-checkpoint continuation only
after a separate semantic confirmation; ordinary tool requests stay in AgentLoop.
"""
import json

from pydantic import BaseModel, ConfigDict

from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel, UnderstandingError


class MissingPlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explicitly_refers_to_saved_plan: bool


async def review_missing_plan_reference(
    provider: HTTPUnderstandingModel, model: str, message: str,
) -> bool | None:
    """True means a confirmed missing saved-plan reference.

    False means the model identifies an ordinary action. None means no reliable
    classification; caller must not invent saved-plan authority.
    """
    prompt = (
        "Independently review the CURRENT user's request; do not assume the "
        "previous classifier is correct. Return only the requested JSON boolean. "
        "True ONLY if the current user asks to execute/continue steps of a "
        "previously saved, persistent execution plan (even when one is absent). "
        "False for standalone calculations, reference lookups, task creation, "
        "plain language drafts, requests to make a new plan, or an unrelated "
        "action; a mere word 'plan' in an explanation is insufficient. "
        "Never infer a saved plan from the request to use a tool. "
        "Do not approve any tool execution or write operation."
    )
    try:
        raw = await provider.complete(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(
                    {"current_request": message}, ensure_ascii=False)},
            ],
            response_schema=MissingPlanReview.model_json_schema(),
        )
        return MissingPlanReview.model_validate_json(raw).explicitly_refers_to_saved_plan
    except (UnderstandingError, ValueError, TypeError):
        return None


def admit_plan_control(
    proposal: PlanRequest, *,
    existing_checkpoint: dict | None,
    missing_reference: bool | None = None,
) -> str | None:
    """Determine whether a plan-control action can be admitted.

    None means no saved-plan control: ordinary execution policy and AgentLoop
    remain responsible for independently authorizing any tool use.
    """
    if proposal.intent not in {"execute_first", "resume"}:
        return None
    if existing_checkpoint is not None:
        return proposal.intent
    if missing_reference is True:
        return "missing_plan"
    return None
