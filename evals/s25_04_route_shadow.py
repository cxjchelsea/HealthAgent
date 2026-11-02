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
        "You are an OFFLINE diagnostic classifier, not an execution router. "
        "Classify the entire CURRENT request (Chinese or English), not an isolated keyword. "
        "ordinary_tool: an independently intelligible standalone request for a to-do/task "
        "RECORD (e.g. create a reminder, checklist entry, or task title), memory, "
        "lookup, calculation, or conceptual explanation, with no executable plan control. "
        "Creating a task RECORD is ordinary_tool even if it names an action or a deadline; "
        "it does NOT create an executable multi-step plan. "
        "plan_control: a SINGLE explicit instruction about an EXECUTABLE PLAN with "
        "ordered steps and saved execution state, to create/save/execute/first-step/"
        "resume/revise/cancel an executable or saved plan. "
        "ambiguous: ANY mixture of plan control plus separate ordinary work; "
        "underspecified continuation, deictic/anaphoric references ('continue previous steps', "
        "'do what we discussed') whose target cannot be verified from this request alone; "
        "or unclear target/intent. A saved_plan boolean only reports existence, never "
        "establishes what the user refers to, and never grants permission. "
        "Assess all requested operations before classifying. If it asks both to persist "
        "an executable plan AND create a separate task record, choose ambiguous, "
        "NOT plan_control and NOT ordinary_tool, even when the first operation is a plan. "
        "A list of to-do records alone is not an executable plan. "
        "Mentioning the *concept* of a plan is ordinary_tool, "
        "not plan_control. A repeated word is not intrinsically ambiguous. "
        "For quote, copy one unique, contiguous, non-empty verbatim substring from the "
        "current request that represents the classification; prefer the entire request "
        "when words occur repeatedly. Do not invent or paraphrase the quote. "
        "Output only the schema. This classification NEVER authorizes any tool or plan."
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
