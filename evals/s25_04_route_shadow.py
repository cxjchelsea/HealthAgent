"""Offline, non-authoritative semantic routing comparison."""
import json
from pydantic import BaseModel, ConfigDict
from typing import Literal


class RouteLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Literal["ordinary_tool", "plan_control", "ambiguous"]
    quote: str


async def shadow_classify(provider, model: str, request: str, saved_plan: bool):
    instructions = (
        "Classify a request for evaluation only: ordinary_tool for standalone "
        "tools, plan_control for saved/executable plan operations, ambiguous "
        "for mixed or unclear requests. Provide an exact quote copied from "
        "the request. This output is not an execution permission."
    )
    try:
        raw = await provider.complete(
            model=model,
            messages=[{"role": "system", "content": instructions},
                      {"role": "user", "content": json.dumps({
                          "request": request, "saved_plan": saved_plan},
                          ensure_ascii=False)}],
            response_schema=RouteLabel.model_json_schema())
        result = RouteLabel.model_validate_json(raw)
        if not result.quote or request.count(result.quote) != 1:
            return {"label": "ambiguous", "reason": "unbound"}
        return {"label": result.label, "reason": "bound"}
    except (ValueError, TypeError, RuntimeError):
        return {"label": "ambiguous", "reason": "invalid"}
