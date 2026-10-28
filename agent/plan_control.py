"""Host admission for model-proposed saved-plan control.

Classification is not authority. Route a missing-checkpoint continuation only
after a separate semantic confirmation; ordinary tool requests stay in AgentLoop.
"""
import json
from dataclasses import dataclass
from typing import Literal

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


class PlanCreationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explicitly_requests_saved_plan: bool


async def review_plan_creation_reference(
    provider: HTTPUnderstandingModel, model: str, message: str,
) -> bool:
    """An independent semantic admission check, not a source of tool authority."""
    prompt = (
        "Return only JSON with explicitly_requests_saved_plan boolean. "
        "True ONLY when the CURRENT user explicitly asks to establish/save a "
        "FUTURE SAVED EXECUTABLE PLAN (one or more steps) without executing it now. "
        "False for an ordinary single lookup, calculation, task creation, "
        "requests with a missing lookup term, questions, explanations, or "
        "written task drafts that do not request an executable plan. "
        "A tool invocation is NOT a request to save an execution plan. "
        "Even an incorrect preliminary model classification must not influence "
        "your answer. This classification does not authorize any tool execution."
    )
    try:
        raw = await provider.complete(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(
                    {"current_request": message}, ensure_ascii=False)},
            ],
            response_schema=PlanCreationReview.model_json_schema(),
        )
        return PlanCreationReview.model_validate_json(raw).explicitly_requests_saved_plan
    except (UnderstandingError, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class PlanAdmission:
    """Final Host plan route. Model intent alone never grants an action."""
    kind: Literal["ordinary", "create", "execute_first", "resume",
                  "missing_plan", "existing_plan"]
    

def resolve_plan_admission(
    proposal: PlanRequest, *, existing_checkpoint: dict | None,
    creation_confirmed: bool = False,
    missing_reference: bool | None = None,
) -> PlanAdmission:
    """Collapse mutually exclusive plan routes without changing state authority.

    The caller separately validates step bindings, plan revision, user
    authorization, and tool permissions. All insufficient proposals stay
    ordinary; they do not become plans through inference.
    """
    if proposal.intent == "plan_only":
        if not creation_confirmed:
            return PlanAdmission("ordinary")
        return PlanAdmission("existing_plan" if existing_checkpoint is not None
                             else "create")
    admitted = admit_plan_control(
        proposal, existing_checkpoint=existing_checkpoint,
        missing_reference=missing_reference)
    return PlanAdmission(admitted if admitted is not None else "ordinary")


def explicit_deferred_plan_request(message: str) -> bool:
    """Recognize explicit non-execution constraint, never authorize a tool."""
    has_plan = any(word in message for word in ("计划", "流程"))
    has_creation = any(word in message for word in ("创建", "建立", "制定", "保存"))
    has_defer = any(word in message for word in (
        "只保存", "暂不执行", "先不执行", "不要执行", "不执行"))
    has_steps = any(word in message for word in ("第一步", "第1步"))
    return has_plan and has_creation and has_defer and has_steps
