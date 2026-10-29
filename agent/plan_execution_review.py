from agent.m5_model_call_metrics import attributed
"""Independent, fail-closed authorization re-review for saved plan continuation."""
import json
import re
from pydantic import BaseModel, ConfigDict
from agent.understanding import HTTPUnderstandingModel, UnderstandingError
from agent.planning import PlanLedger


def explicitly_forbids_saved_plan_execution(message: str) -> bool:
    """A negative instruction is a hard veto, never a positive authorization."""
    negative = r"(?:不要|不准|禁止|别|先不|暂不|do\\s+not|don\u0027t)"
    action = r"(?:执行|运行|继续|跑|execute|run|resume)"
    referent = r"(?:计划|步骤|plan|steps)"
    return bool(re.search(negative + r".{0,24}" + action + r".{0,24}" + referent
                          + "|" + negative + r".{0,24}" + referent
                          + r".{0,20}" + action, message, re.IGNORECASE))

class PlanExecutionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explicitly_authorizes_pending_steps: bool


@attributed("plan_execution_authorization")
async def review_plan_execution_authorization(
    provider: HTTPUnderstandingModel, model: str, current_message: str,
    checkpoint: dict, intent: str,
) -> bool:
    if intent not in {"execute_first", "resume"} or explicitly_forbids_saved_plan_execution(current_message):
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
        "the CURRENT user explicitly instructs you to execute the saved plan "
        "within the requested scope. IMPORTANT: requested_plan_action=execute_first "
        "means the user asks to run ONLY the first saved step; authorization of "
        "that one step is TRUE even if the user expressly says not to run the "
        "other steps. It does not require authorization for ALL pending steps. "
        "requested_plan_action=resume means the user authorizes executing "
        "remaining steps. An instruction to merely inspect, discuss, draft, "
        "cancel, revise, or do an independent tool calculation is FALSE. "
        "A negative instruction about executing the entire plan does NOT "
        "cancel a positive authorization to run the explicitly selected first "
        "step, but a standalone request saying NOT to execute the saved plan "
        "is FALSE. Judge the full current message, not isolated negation words. "
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
