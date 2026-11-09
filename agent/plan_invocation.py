"""Non-executing S25-05 plan binding inspection.

A PlanInvocation is diagnostic data, NEVER an execution authorization. It cannot
be passed as an ordinary Host grant; M4 CAS, snapshot guards, journals and
receipts still determine whether a saved-plan step may execute.
"""
from dataclasses import dataclass
from typing import Literal

from agent.plan_intent import READ_TOOLS
from agent.planning import PlanLedger
from agent.tools import SCHEMAS
from services.evidence_store import arguments_digest

PlanControl = Literal["execute_first", "resume"]


@dataclass(frozen=True)
class PlanInvocation:
    plan_revision: int
    step_id: int
    control: PlanControl
    tool_name: str
    arguments_digest: str
    tool_version: None = None  # Historical checkpoints have no version binding.


def inspect_plan_invocations(
    checkpoint: dict, control: PlanControl,
) -> tuple[PlanInvocation, ...]:
    """Validate immutable plan binding descriptions, with no side effects.

    Fail closed for malformed or unbound plan steps. This function does not
    check owner identity, the active Turn or the M4 execution guard, and cannot
    authorize execution. It intentionally never guesses a tool version.
    """
    if control not in ("execute_first", "resume"):
        raise ValueError("invalid_plan_control")
    ledger = PlanLedger.restore(checkpoint)
    pending = [step for step in ledger.snapshot() if step["status"] == "pending"]
    if not pending:
        raise ValueError("plan_has_no_pending_steps")
    result = []
    for step in pending:
        name = step.get("expected_tool")
        args = step.get("expected_arguments")
        if not isinstance(name, str) or name not in READ_TOOLS or not isinstance(args, dict):
            raise ValueError("invalid_plan_binding")
        if name not in SCHEMAS:
            raise ValueError("invalid_plan_binding")
        try:
            validated = SCHEMAS[name].model_validate(args).model_dump()
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_plan_arguments") from exc
        result.append(PlanInvocation(
            plan_revision=ledger.revision,
            step_id=step["id"],
            control=control,
            tool_name=name,
            arguments_digest=arguments_digest(validated),
        ))
    return tuple(result[:1] if control == "execute_first" else result)
