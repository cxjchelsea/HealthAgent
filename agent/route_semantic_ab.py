"""Diagnostic A/B semantic classifiers. No tools or execution."""
import asyncio
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict

class CoreIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_type: Literal["none", "task", "plan", "plan_step", "pending_slot", "tool"]
    operation: Literal["answer", "clarify", "create", "revise", "execute_first", "resume", "cancel", "invoke"]

PROMPT = """Classify the user request object and operation. A task means a
persisted task-manager record, not any generic action. Calculation or retrieval
means tool/invoke. Drafting without saving means none/answer. A pending slot
exists only if a Host pending request exists. Cancel is different from invoke.
Executing a first saved step is different from resuming remaining steps."""

async def classify(provider, model, message, facts, timeout=60):
    raw = await asyncio.wait_for(provider.complete(
        model=model, response_schema=CoreIntent.model_json_schema(),
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": json.dumps({
                      "message": message, "has_saved_plan": facts.has_plan,
                      "has_pending_action": bool(facts.pending_action)},
                      ensure_ascii=False)}]), timeout=timeout)
    return CoreIntent.model_validate_json(raw)
