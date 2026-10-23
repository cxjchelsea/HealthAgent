"""Independent, fail-closed authorization re-review for saved plan continuation."""
import json
from pydantic import BaseModel, ConfigDict
from agent.understanding import HTTPUnderstandingModel, UnderstandingError
from agent.planning import PlanLedger


class PlanExecutionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explicitly_authorizes_pending_steps: bool


async def review_plan_execution_authorization(
    provider: HTTPUnderstandingModel, model: str, current_message: str,
    checkpoint: dict, intent: str,
) -> bool:
    if intent not in {"execute_first", "resume"}:
        return False
    try:
        ledger = PlanLedger.restore(checkpoint)
    except (ValueError, TypeError):
        return False
    pending = [s for s in ledger.snapshot() if s["status"] == "pending"]
    allowed = {"calculate", "mcp_reference_lookup", "utc_time", "list_tasks", "recall_memory"}
    if not pending or any(
        s.get("expected_tool") not in allowed or not isinstance(s.get("expected_arguments"), dict)
        for s in pending
    ):
        return False
    system = (
        "Return JSON {explicitly_authorizes_pending_steps: boolean}. True only when "
        "the CURRENT user explicitly instructs you to execute the still pending "
        "steps of the already saved plan. An instruction to merely inspect, discuss, "
        "draft, pause, cancel, or revise is false. 'Continue remaining steps' counts "
        "as explicit execution authorization when a pending plan exists. "
        "Do not infer authorization from the old plan itself. This is a read-only "
        "execution check, not permission for writes or new tools."
    )
    try:
        raw = await provider.complete(
            model=model, messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({
                    "current_message": current_message,
                    "requested_plan_action": intent,
                    "pending_step_count": len(pending),
                    "pending_step_goals": [s["goal"] for s in pending],
                }, ensure_ascii=False)},
            ], response_schema=PlanExecutionReview.model_json_schema(),
        )
        return PlanExecutionReview.model_validate_json(raw).explicitly_authorizes_pending_steps
    except (UnderstandingError, ValueError, TypeError):
        return False
