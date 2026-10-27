"""Extract bounded, literal, ordered action spans from plan-create requests."""
import asyncio
import json
from pydantic import BaseModel, ConfigDict, Field
from agent.route_shadow import QuoteEvidence

class OrderedSteps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Legacy two-field decoder remains accepted for prior frozen fixtures.
    first: str | None = Field(default=None, max_length=400)
    second: str | None = Field(default=None, max_length=400)
    steps: list[str] = Field(default_factory=list, max_length=8)

PROMPT = """Identify 2 to 8 DISTINCT ordered actions explicitly expressed in
the current message. Return their exact contiguous verbatim substrings in
source order in steps; do not infer or paraphrase actions or include
general plan framing language. If you cannot establish at least two distinct
grounded actions, return an empty list. first/second are backward-compatible
optional fields and may be null. No inferred authority or new plan steps."""

async def collect_ordered_step_quotes(provider, model, message, timeout=60):
    raw = await asyncio.wait_for(provider.complete(
        model=model, response_schema=OrderedSteps.model_json_schema(),
        messages=[{"role":"system","content":PROMPT},
                  {"role":"user","content":json.dumps({"message":message},
                                                    ensure_ascii=False)}]), timeout=timeout)
    obj = OrderedSteps.model_validate_json(raw)
    spans = obj.steps if obj.steps else [obj.first, obj.second]
    if not 2 <= len(spans) <= 8 or any(not span for span in spans):
        return []
    if len(set(spans)) != len(spans):
        return []
    # Every evidence item must be unique in the ORIGINAL message and
    # appear in strictly non-overlapping source order.
    previous_end = 0
    for span in spans:
        if message.count(span) != 1:
            return []
        start = message.index(span)
        if start < previous_end:
            return []
        previous_end = start + len(span)
    return [QuoteEvidence(exact_text=span) for span in spans]
