from agent.m5_model_call_metrics import attributed
"""Independent scope guard for plan execution.

Never permits more steps if the request is explicitly limited to the first.
Fail closed for ambiguous scope.
"""
import json
from pydantic import BaseModel, ConfigDict
from agent.understanding import HTTPUnderstandingModel, UnderstandingError

class ScopeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str


@attributed("plan_execution_scope")
async def review_plan_execution_scope(provider: HTTPUnderstandingModel, model: str,
                                      message: str) -> str:
    try:
        raw = await provider.complete(model=model, messages=[
            {"role": "system", "content":
             "Classify how many saved plan steps the CURRENT user authorizes executing. "
             "Return JSON scope='one' ONLY for an explicit limit to exactly the "
             "first/next step or explicit hold of subsequent steps. Return scope='all' "
             "ONLY when the current user explicitly asks to run the whole saved plan "
             "or all remaining steps. Return scope='unclear' otherwise. "
             "Do not derive authorization from earlier messages or the mere existence "
             "of a plan."},
            {"role": "user", "content": json.dumps(
                {"current_request": message}, ensure_ascii=False)}
        ], response_schema=ScopeReview.model_json_schema())
        scope = ScopeReview.model_validate_json(raw).scope
        return scope if scope in {"one", "all"} else "unclear"
    except (UnderstandingError, ValueError, TypeError):
        return "unclear"
