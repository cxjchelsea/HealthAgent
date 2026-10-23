"""Bounded review of spurious understanding clarification for read-only tools.

A positive proposal only resumes the normal AgentLoop with read-only exposure;
it cannot grant write authority, invent parameters, or bypass tool validation.
"""
import json

from pydantic import BaseModel, ConfigDict

from agent.understanding import HTTPUnderstandingModel, UnderstandingError


class ReadOnlyClarificationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete_explicit_read_only_request: bool


async def review_read_only_clarification(
    provider: HTTPUnderstandingModel, model: str, message: str,
    question: str, available_read_tools: list[str],
) -> bool:
    prompt = (
        "Independently assess whether the CURRENT user's request already contains "
        "the information needed to ATTEMPT an available READ-ONLY tool operation. "
        "Return JSON with complete_explicit_read_only_request boolean. True ONLY "
        "when the user explicitly asks to use an available read-only tool, provides "
        "all necessary arguments, and does not ask for a write or other side effect. "
        "A mathematically invalid expression can still be attempted by a calculator "
        "and produce a real failure observation; it does not need clarification "
        "about whether to explain the failure. False for underspecified reference "
        "lookups, missing operands, ambiguous referents, task/memory writes, "
        "hypotheticals, or uncertain authorization. Use the supplied registered "
        "read-only tools, not invented capabilities. This review NEVER authorizes "
        "writes, new tools, or successful results."
    )
    try:
        raw = await provider.complete(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({
                    "current_request": message,
                    "proposed_clarification": question,
                    "available_read_only_tools": available_read_tools,
                }, ensure_ascii=False)},
            ],
            response_schema=ReadOnlyClarificationReview.model_json_schema(),
        )
        return ReadOnlyClarificationReview.model_validate_json(
            raw).complete_explicit_read_only_request
    except (UnderstandingError, ValueError, TypeError):
        return False
