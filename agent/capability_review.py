"""Semantic tool reference proposal; registry is the sole availability authority.

This classifier may propose canonical tool IDs but cannot define what exists,
authorize execution, or deny a registered capability by its localized name.
"""
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.understanding import HTTPUnderstandingModel, UnderstandingError


class CapabilityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explicitly_requested_tool: str | None = Field(default=None, max_length=80)
    request_is_clear: bool
    resolved_tool_id: str | None = Field(default=None, max_length=80)


def host_unsupported_capability(
    review: CapabilityReview, message: str, registered_names: set[str],
) -> str | None:
    """Only reject a clearly named absent tool, never a registered tool.

    Model-proposed canonical references can *prevent a false denial*, not grant
    access to an unregistered tool. Real authorization occurs downstream.
    """
    if not review.request_is_clear or not review.explicitly_requested_tool:
        return None
    registered = {x.casefold() for x in registered_names}
    if review.resolved_tool_id and review.resolved_tool_id.casefold() in registered:
        return None
    requested = review.explicitly_requested_tool.strip()
    if (not requested or requested.casefold() not in message.casefold()
            or requested.casefold() in registered):
        return None
    return requested


async def review_unsupported_capability(
    provider: HTTPUnderstandingModel, model: str,
    message: str, allowed_names: set[str],
) -> str | None:
    prompt = (
        "Identify a clearly requested named software tool or system capability. "
        "Return JSON fields explicitly_requested_tool (literal name from user "
        "or null), request_is_clear (boolean), resolved_tool_id (canonical "
        "registered ID or null). Canonical IDs must be selected ONLY from "
        "the given available_tools list using their meanings. For example, "
        "a Chinese request for a calculation tool can refer to 'calculate'. "
        "For a shell command when no shell tool is registered, "
        "resolved_tool_id=null. Do not invent tool IDs or infer authorization. "
        "For explanations, hypotheticals, generic 'a tool' references and "
        "unclear requests, request_is_clear=false. No tool is executed here."
    )
    try:
        raw = await provider.complete(
            model=model, messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({
                    "current_message": message,
                    "available_tools": sorted(allowed_names),
                }, ensure_ascii=False)},
            ],
            response_schema=CapabilityReview.model_json_schema())
        review = CapabilityReview.model_validate_json(raw)
    except (UnderstandingError, ValueError, ValidationError, TypeError):
        return None
    return host_unsupported_capability(review, message, allowed_names)
