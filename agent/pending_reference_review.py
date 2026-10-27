"""Admission of a missing argument for an explicitly requested read-only MCP lookup.

The decision only establishes a pending clarification; it does not execute
tools. The current user's request must independently name MCP as its source.
"""
import json
from pydantic import BaseModel, ConfigDict
from agent.understanding import HTTPUnderstandingModel, UnderstandingError


class PendingReferenceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explicitly_requests_mcp_lookup_without_term: bool


async def review_pending_reference_lookup(
    provider: HTTPUnderstandingModel, model: str, message: str,
) -> bool:
    # Source anchoring is necessary but never sufficient for this admission.
    if "mcp" not in message.casefold():
        return False
    prompt = (
        "Classify the CURRENT user request, return only JSON with "
        "explicitly_requests_mcp_lookup_without_term boolean. True ONLY if "
        "the user actually requested a lookup using the MCP reference tool "
        "but did not supply any identifiable reference term. False if a "
        "reference term was supplied, this is a question about MCP, a "
        "hypothetical, a different action, or the request is unclear. "
        "This cannot authorize execution, it only preserves a pending "
        "read-only term-clarification state."
    )
    try:
        raw = await provider.complete(
            model=model,
            messages=[{"role": "system", "content": prompt},
                      {"role": "user", "content": json.dumps(
                          {"current_request": message}, ensure_ascii=False)}],
            response_schema=PendingReferenceReview.model_json_schema())
        return PendingReferenceReview.model_validate_json(
            raw).explicitly_requests_mcp_lookup_without_term
    except (UnderstandingError, ValueError, TypeError):
        return False
