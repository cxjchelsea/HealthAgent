"""Version contract for the existing saved-plan executor, not the Registry.

Every release changing the semantics of agent.tools.execute_tool or its
saved-plan adapters must bump this version before accepting old pinned plans.
"""
from agent.planning import PlanLedger

LEGACY_PLAN_EXECUTOR_VERSION = "legacy-plan-v1"


def pin_new_plan_checkpoint(checkpoint: dict) -> dict:
    """Host-only creation-time pin; do not call for historical/restored plans."""
    ledger = PlanLedger.restore(checkpoint)
    steps = ledger.snapshot()
    if any(step["status"] != "pending"
           or step.get("tool_version") is not None
           or not step.get("expected_tool") for step in steps):
        raise ValueError("plan_version_pin_requires_new_plan")
    return {**checkpoint, "steps": [
        {**step, "tool_version": LEGACY_PLAN_EXECUTOR_VERSION}
        for step in steps]}


def plan_versions_compatible(checkpoint: dict, control: str) -> bool:
    """Reject tampering and executor upgrades before any saved-plan dispatch.

    A historical checkpoint with NO version fields remains legacy-compatible,
    but is never represented as version-pinned. Mixed/explicit-NULL values
    are rejected to prevent silent downgrade of a pinned checkpoint.
    """
    if control not in ("execute_first", "resume"):
        return False
    try:
        steps = PlanLedger.restore(checkpoint).snapshot()
    except (ValueError, TypeError, KeyError):
        return False
    pending = [step for step in steps if step["status"] == "pending"]
    if not pending:
        return False
    versioned = ["tool_version" in step for step in steps]
    if any(versioned) and not all(versioned):
        return False
    if any(versioned):
        if any(step["tool_version"] != LEGACY_PLAN_EXECUTOR_VERSION
               for step in steps):
            return False
    return True
