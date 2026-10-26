"""Second-stage reference extraction for controlled M2 comparison."""
import asyncio
import json
from pydantic import BaseModel, ConfigDict, Field

class EvidenceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_quote: str | None = Field(default=None, max_length=400)
    reference_text: str | None = Field(default=None, max_length=200)
    step_number: int | None = Field(default=None, ge=1, le=8)
    plan_steps: list[str] = Field(default_factory=list, max_length=8)
    clarification_question: str | None = Field(default=None, max_length=300)

PROMPT = """Given the already-classified object and operation, extract only
literal current-message evidence. New plans need separately quoted steps.
Step revisions need an explicit step number and literal reference quotation.
Do not invent references, offsets, steps, checkpoint contents or permissions."""

async def extract(provider, model, message, core, timeout=60):
    raw = await asyncio.wait_for(provider.complete(
        model=model, response_schema=EvidenceDraft.model_json_schema(),
        messages=[{"role":"system","content":PROMPT},
                  {"role":"user","content":json.dumps({
                      "message":message,"object_type":core.object_type,
                      "operation":core.operation},ensure_ascii=False)}]),
        timeout=timeout)
    return EvidenceDraft.model_validate_json(raw)

def needed(core):
    return (core.object_type, core.operation) in {
        ("plan","create"),("plan_step","revise"),("none","clarify")}
