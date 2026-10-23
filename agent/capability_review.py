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
    matches_available_capability: bool = False


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
        "generic reference to 'a tool' is not. Natural-language references to "
        "an available capability are NOT unsupported tools. Set "
        "matches_available_capability=true when the user refers to any "
        "registered capability, including in another language. Only classify; "
        "never authorize."
    )
    try:
        raw = await provider.complete(
            model=model, messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(
                    {"current_message": message, "available_tools": sorted(allowed_names)}, ensure_ascii=False)}],
            response_schema=CapabilityReview.model_json_schema())
        review = CapabilityReview.model_validate_json(raw)
    except (UnderstandingError, ValueError, ValidationError, TypeError):
        return None
    if (not review.request_is_clear or review.matches_available_capability
            or not review.explicitly_requested_tool):
        return None
    requested = review.explicitly_requested_tool.strip()
    if (not requested or requested.casefold() not in message.casefold()
            or requested.casefold() in {x.casefold() for x in allowed_names}):
        return None
    # A model's proposed non-allowlisted name can never enable execution.
    return requested
