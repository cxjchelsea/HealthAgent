"""Extract two literal ordered user step spans for M2 plan-create previews."""
import asyncio
import json
from pydantic import BaseModel, ConfigDict, Field
from agent.route_shadow import QuoteEvidence

class OrderedSteps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first: str | None = Field(default=None, max_length=400)
    second: str | None = Field(default=None, max_length=400)

PROMPT = """Return two distinct verbatim action spans from the message,
in source order. Use null when either span is missing or uncertain.
Do not produce a route, inferred plan, or permission."""

async def collect_ordered_step_quotes(provider, model, message, timeout=60):
    raw = await asyncio.wait_for(provider.complete(
        model=model, response_schema=OrderedSteps.model_json_schema(),
        messages=[{"role":"system","content":PROMPT},
                  {"role":"user","content":json.dumps({"message":message},
                                                    ensure_ascii=False)}]), timeout=timeout)
    obj = OrderedSteps.model_validate_json(raw)
    first, second = obj.first, obj.second
    if not first or not second or first == second:
        return []
    if message.count(first) != 1 or message.count(second) != 1:
        return []
    if message.index(first) + len(first) > message.index(second):
        return []
    return [QuoteEvidence(exact_text=first), QuoteEvidence(exact_text=second)]
