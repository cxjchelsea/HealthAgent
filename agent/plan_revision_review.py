"""Guarded plan revision proposal: validate exact target and prior binding; never execute."""
from agent.m5_model_call_metrics import attributed
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict
from agent.planning import PlanLedger
from agent.understanding import HTTPUnderstandingModel, UnderstandingError
from agent.tools import SCHEMAS


class RevisionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["revise", "other"]
    edit_requested: bool | None = None
    step_number: int | None = None
    expected_tool: str | None = None
    old_argument: str | None = None
    new_argument: str | None = None


@attributed("plan_revision")
async def review_plan_revision(provider: HTTPUnderstandingModel, model: str,
                               message: str, checkpoint: dict) -> RevisionProposal:
    steps = PlanLedger.restore(checkpoint).snapshot()
    try:
        raw = await provider.complete(model=model, messages=[
        {"role": "system", "content":
         "Return JSON matching schema. Classify a request to EDIT an existing saved "
         "plan as revise, otherwise other. Also set edit_requested=true "
         "whenever the user attempts to edit a saved plan, even if the exact "
         "step binding is unclear; false for execute/resume/status/chat. "
         "For revise identify explicit 1-based "
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
        proposal = RevisionProposal.model_validate_json(raw)
        if proposal.intent == "revise" and not (
                proposal.old_argument and proposal.new_argument
                and proposal.old_argument.casefold() in message.casefold()
                and proposal.new_argument.casefold() in message.casefold()):
            return RevisionProposal(intent="other", edit_requested=proposal.edit_requested)
        return proposal
    except (UnderstandingError, ValueError, TypeError):
        return RevisionProposal(intent="other")


def apply_plan_revision(checkpoint: dict, message: str,
                        proposal: RevisionProposal) -> dict | None:
    """Return updated checkpoint or None for conflict/uncertainty; no execution."""
    ledger = PlanLedger.restore(checkpoint)
    steps = ledger.snapshot()
    n = proposal.step_number
    if (proposal.intent != "revise" or type(n) is not int
            or n < 1 or n > len(steps)):
        return None
    # Step numbering is checked against explicit user wording, not model claims.
    ordinals = {"一": 1, "二": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8}
    mentions = list(re.finditer(r"第([一二三四五六七八1-8])步", message))
    # An explicit unchanged-step clause is a preserve constraint, not an
    # alternative revision target. Fail closed for any additional step mention
    # that is not *immediately* marked unchanged; never reinterpret its intent.
    target_mentioned = False
    for mention in mentions:
        token = mention.group(1)
        mentioned_n = ordinals[token] if token in ordinals else int(token)
        if mentioned_n == n:
            target_mentioned = True
            continue
        after = message[mention.end():]
        if not re.match(r"\s*(?:(?:保持|维持)\s*)?(?:不变|原样|无需修改|不修改)(?=$|[，。；,;！!\s])", after):
            return None
    if mentions and not target_mentioned:
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

# A failed proposal must not be treated as permission to mutate a plan.

class PlanEditIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edit_requested: bool


async def check_plan_edit_intent(provider, model, message, checkpoint):
    steps = PlanLedger.restore(checkpoint).snapshot()
    try:
        raw = await provider.complete(model=model, messages=[
            {"role": "system", "content":
             "Classify the CURRENT request. Return JSON edit_requested boolean. "
             "True for requests to change saved plan steps, including conflicting "
             "step references. False for run, execute, resume or ordinary chat."},
            {"role": "user", "content": json.dumps({
                "request": message,
                "saved_steps": [x["goal"] for x in steps]}, ensure_ascii=False)}
        ], response_schema=PlanEditIntent.model_json_schema())
        return PlanEditIntent.model_validate_json(raw).edit_requested
    except (UnderstandingError, ValueError, TypeError):
        return False


def explicit_step_binding_conflict(message: str, checkpoint: dict) -> bool:
    """Conservative conflict-only gate for a stated step and two expressions.

    It never applies a revision. An ambiguous request is left to model review.
    """
    steps = PlanLedger.restore(checkpoint).snapshot()
    ordinals = {"一": 1, "二": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8}
    refs = re.findall(r"第([一二三四五六七八1-8])步", message)
    if len(refs) != 1:
        return False
    step_number = ordinals.get(refs[0], int(refs[0]) if refs[0].isdigit() else -1)
    if not 1 <= step_number <= len(steps):
        return False
    # Strict grammar, not general intent recognition: require a concrete
    # before/after arithmetic change in the same message.
    expressions = re.findall(r"(?<![\w.])\d+(?:\s*[+*/-]\s*\d+)+(?![\w.])", message)
    if len(expressions) != 2:
        return False
    if not re.search(r"(?:不是|改为|修改为|换成|而是)", message):
        return False
    target = steps[step_number - 1]
    old = expressions[0].replace(" ", "")
    if target.get("expected_tool") != "calculate":
        return True
    return target.get("expected_arguments", {}).get("expression") != old
