"""Clarification vs. unavailable named tool: model-assisted, allowlist-bound.

A denial never invokes a tool. Unclear classification retains clarification.
"""
import json
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from agent.understanding import HTTPUnderstandingModel, UnderstandingError


class CapabilityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explicitly_requested_tool: str | None = Field(default=None, max_length=80)
    request_is_clear: bool


async def review_unsupported_capability(provider: HTTPUnderstandingModel,
                                        model: str, message: str,
                                        allowed_names: set[str]) -> str | None:
    prompt = (
        "Classify only whether the CURRENT user clearly requests the actual execution "
        "of a specifically named software tool or system capability. Return JSON "
        "with explicitly_requested_tool (tool identifier/name or null) and "
        "request_is_clear (boolean). A question, explanation, hypothetical or "
        "ambiguous request is NOT a clear execution request. Do not invent a "
        "tool, alias or capability. A specific named system shell is a tool; a "
        "generic reference to 'a tool' is not. Only classify, never authorize."
    )
    try:
        raw = await provider.complete(
            model=model, messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(
                    {"current_message": message}, ensure_ascii=False)}],
            response_schema=CapabilityReview.model_json_schema())
        review = CapabilityReview.model_validate_json(raw)
    except (UnderstandingError, ValueError, ValidationError):
        return None
    if not review.request_is_clear or not review.explicitly_requested_tool:
        return None
    requested = review.explicitly_requested_tool.strip()
    if not requested or requested.casefold() in {x.casefold() for x in allowed_names}:
        return None
    # A model's proposed non-allowlisted name can never enable execution.
    return requested
