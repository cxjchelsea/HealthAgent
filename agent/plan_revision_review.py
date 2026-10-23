"""Guarded plan revision proposal: validate exact target and prior binding; never execute."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict
from agent.planning import PlanLedger
from agent.understanding import HTTPUnderstandingModel, UnderstandingError
from agent.tools import SCHEMAS


class RevisionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["revise", "other"]
    step_number: int | None = None
    expected_tool: str | None = None
    old_argument: str | None = None
    new_argument: str | None = None


async def review_plan_revision(provider: HTTPUnderstandingModel, model: str,
                               message: str, checkpoint: dict) -> RevisionProposal:
    steps = PlanLedger.restore(checkpoint).snapshot()
    raw = await provider.complete(model=model, messages=[
        {"role": "system", "content":
         "Return JSON matching schema. Classify a request to EDIT an existing saved "
         "plan as revise, otherwise other. For revise identify explicit 1-based "
         "step_number, expected_tool, old_argument and new_argument from CURRENT "
         "user words ONLY, not from guesses. When the user cites an old expression "
         "and a new expression, preserve both exactly. Do NOT correct contradictions "
         "with the saved steps. Do not execute or claim completion. "
         "If any parameter is uncertain use null."},
        {"role": "user", "content": json.dumps(
            {"current_message": message, "saved_steps": [
                {"step_number": i + 1, "goal": s["goal"],
                 "tool": s.get("expected_tool"),
                 "arguments": s.get("expected_arguments"), "status": s["status"]}
                for i, s in enumerate(steps)]}, ensure_ascii=False)}
    ], response_schema=RevisionProposal.model_json_schema())
    try:
        return RevisionProposal.model_validate_json(raw)
    except ValueError as exc:
        raise UnderstandingError("plan_revision_intent_invalid") from exc


def apply_plan_revision(checkpoint: dict, message: str,
                        proposal: RevisionProposal) -> dict | None:
    """Return updated checkpoint or None for conflict/uncertainty; no execution."""
    ledger = PlanLedger.restore(checkpoint)
    steps = ledger.snapshot()
    n = proposal.step_number
    if (proposal.intent != "revise" or type(n) is not int
            or n < 1 or n > len(steps)):
        return None
    target = steps[n - 1]
    if (target["status"] != "pending"
            or proposal.expected_tool != "calculate"
            or target.get("expected_tool") != "calculate"
            or not proposal.old_argument or not proposal.new_argument):
        return None
    old = target["expected_arguments"].get("expression")
    if old != proposal.old_argument:
        return None
    if (proposal.old_argument.casefold() not in message.casefold()
            or proposal.new_argument.casefold() not in message.casefold()):
        return None
    try:
        replacement = SCHEMAS["calculate"].model_validate(
            {"expression": proposal.new_argument}).model_dump()
    except ValueError:
        return None
    # Preserve all completed steps and unaffected pending steps, including IDs.
    # No tool execution; only a single pending binding is revised.
    ledger.steps[n - 1] = {**target,
        "goal": "计算 " + replacement["expression"],
        "expected_arguments": replacement}
    ledger.revision += 1
    ledger.events.append({"event": "plan_step_revised", "revision": ledger.revision,
                          "id": target["id"], "source": "user_verified_binding"})
    return {"checkpoint": ledger.checkpoint(), "event": ledger.events[-1]}
