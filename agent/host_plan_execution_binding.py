"""Host-bound plan execution descriptor; never a standalone dispatch permit.

Only the API Host can establish trustworthy owner/Turn facts. This typed record
captures admitted intent for later compatibility checks, but M4 claim/CAS,
journal, checkpoint and request ownership are still mandatory at call time.
"""
import hashlib
import json
from dataclasses import dataclass

from agent.plan_invocation import PlanInvocation, inspect_plan_invocations
from agent.primary_host_admission_v1 import PrimaryHostDecision


@dataclass(frozen=True)
class HostPlanExecutionBinding:
    owner_id: str
    conversation_id: str
    turn_id: str
    request_id: str
    checkpoint_digest: str
    plan_revision: int
    control: str
    invocations: tuple[PlanInvocation, ...]


def _checkpoint_digest(checkpoint_raw: str) -> str:
    return hashlib.sha256(checkpoint_raw.encode("utf-8")).hexdigest()


def bind_host_plan_execution(
    *, decision: PrimaryHostDecision,
    owner_id: str, conversation_id: str, turn_id: str, request_id: str,
    checkpoint_raw: str, checkpoint: dict,
) -> HostPlanExecutionBinding:
    """Fail closed for mismatched Host admission and saved-plan descriptors.

    No DB reads or effects occur. The caller must separately validate the
    owner-scoped Turn and acquire the exact raw-snapshot M4 execution claim.
    """
    if (decision.disposition != "allow"
            or decision.execution_mode != "read_only"
            or decision.reason != "primary_v1_plan_execution_candidate"
            or decision.route_kind not in {"plan_execute_first", "plan_resume"}
            or decision.plan_control not in {"execute_first", "resume"}):
        raise ValueError("host_plan_admission_required")
    expected_route = ("plan_execute_first" if decision.plan_control == "execute_first"
                      else "plan_resume")
    if decision.route_kind != expected_route:
        raise ValueError("host_plan_control_mismatch")
    if not all(isinstance(value, str) and value.strip()
               for value in (owner_id, conversation_id, turn_id, request_id)):
        raise ValueError("host_plan_identity_missing")
    if not isinstance(checkpoint_raw, str):
        raise ValueError("host_plan_snapshot_missing")
    try:
        parsed = json.loads(checkpoint_raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("host_plan_snapshot_invalid") from exc
    if parsed != checkpoint or type(parsed) is not dict:
        raise ValueError("host_plan_snapshot_mismatch")
    invocations = inspect_plan_invocations(parsed, decision.plan_control)
    return HostPlanExecutionBinding(
        owner_id=owner_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        request_id=request_id,
        checkpoint_digest=_checkpoint_digest(checkpoint_raw),
        plan_revision=invocations[0].plan_revision,
        control=decision.plan_control,
        invocations=invocations,
    )


def host_plan_binding_matches(
    *, decision: PrimaryHostDecision,
    owner_id: str, conversation_id: str, turn_id: str, request_id: str,
    checkpoint_raw: str | None, checkpoint: dict | None,
    expected_control: str,
) -> bool:
    """Additional Host-side rejection gate, NOT a durable execution permit."""
    if checkpoint_raw is None or checkpoint is None:
        return False
    try:
        bound = bind_host_plan_execution(
            decision=decision, owner_id=owner_id,
            conversation_id=conversation_id, turn_id=turn_id,
            request_id=request_id, checkpoint_raw=checkpoint_raw,
            checkpoint=checkpoint,
        )
    except (ValueError, TypeError, KeyError):
        return False
    return (bound.control == expected_control
            and bound.plan_revision == checkpoint.get("revision")
            and bool(bound.invocations))
