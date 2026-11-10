"""S25-06 guarded serial read runner, opt-in and not wired to HTTP.

Only the existing Primary/Host admission and M4 durable Journal are used.
Each invoked tool gets exactly one durable pre-dispatch row, then a bounded
receipt, then an atomic per-step checkpoint. No unknown outcome is replayed.
"""
import json
from dataclasses import dataclass

from agent.config import Settings
from agent.dependency_plan_host import bind_dependency_plan_read
from agent.dependency_plan_schema import DependencyPlanError
from agent.primary_host_admission_v1 import PrimaryHostDecision
from agent.tool_registry import READ_REGISTRY, ReadToolRuntime
from services.dependency_dispatch import begin_dependency_dispatch_in_session
from services.dependency_execution import checkpoint_dependency_step_in_session
from services.plan_dispatch import PlanDispatchJournal


@dataclass(frozen=True)
class DependencyReadOutcome:
    status: str
    dispatch_id: str
    checkpoint: dict | None
    error: str | None = None


def run_one_dependency_read(
    session_factory, *, settings: Settings, decision: PrimaryHostDecision,
    owner_id: str, conversation_id: str, turn_id: str, request_id: str,
    initial_turn_raw: str, current_raw: str, control: str, step_id: int,
    runtime: ReadToolRuntime | None = None,
) -> DependencyReadOutcome:
    """One bounded, version-pinned saved-plan read; never a standalone permit.

    The caller MUST be the authenticated HTTP Host and must have recorded the
    original immutable Primary admission. Feature flag is OFF by default.
    """
    prepared = bind_dependency_plan_read(
        session_factory, settings=settings, decision=decision,
        owner_id=owner_id, conversation_id=conversation_id,
        turn_id=turn_id, request_id=request_id,
        expected_raw=current_raw, control=control, step_id=step_id)
    with session_factory.begin() as db:
        dispatch_id = begin_dependency_dispatch_in_session(
            db, binding=prepared, expected_raw=current_raw,
            initial_turn_raw=initial_turn_raw)
    # The committed dispatch_unknown row is the mandatory before-call fence.
    # It is intentionally NOT deleted or replayed if adapter/receipt fails.
    observed = READ_REGISTRY.invoke(
        prepared.tool_name, prepared.arguments,
        granted_names=(prepared.tool_name,),
        exposed_names=(prepared.tool_name,),
        expected_version=prepared.tool_version,
        runtime=runtime)
    journal = PlanDispatchJournal(session_factory, conversation_id, turn_id)
    journal.record_receipt(dispatch_id, observed)
    if observed.get("ok") is not True:
        return DependencyReadOutcome(
            status="blocked", dispatch_id=dispatch_id, checkpoint=None,
            error=str(observed.get("error", "tool_execution_error"))[:80])
    with session_factory.begin() as db:
        checkpoint = checkpoint_dependency_step_in_session(
            db, binding=prepared, expected_raw=current_raw,
            dispatch_id=dispatch_id)
    return DependencyReadOutcome(
        status="checkpointed", dispatch_id=dispatch_id, checkpoint=checkpoint)


def run_dependency_read_scope(
    session_factory, *, settings: Settings, decision: PrimaryHostDecision,
    owner_id: str, conversation_id: str, turn_id: str, request_id: str,
    initial_turn_raw: str, control: str,
    runtime: ReadToolRuntime | None = None,
) -> tuple[DependencyReadOutcome, ...]:
    """Execute first-step-only OR bounded remaining steps in one authorized Turn.

    The Host remains responsible for the terminal Turn/message transaction and
    release of M4 Claim. Caller never retries an unknown/observed step.
    """
    if control not in ("execute_first", "resume"):
        raise DependencyPlanError("dependency_host_control_denied")
    current_raw = initial_turn_raw
    results = []
    for _ in range(8):
        try:
            checkpoint = json.loads(current_raw)
        except (TypeError, ValueError) as exc:
            raise DependencyPlanError("dependency_invalid_schema") from exc
        if type(checkpoint) is not dict or type(checkpoint.get("steps")) is not list:
            raise DependencyPlanError("dependency_invalid_schema")
        pending = [s for s in checkpoint["steps"]
                   if type(s) is dict and s.get("status") == "pending"]
        if not pending:
            return tuple(results)
        step_id = pending[0].get("id")
        if type(step_id) is not int:
            raise DependencyPlanError("dependency_invalid_schema")
        outcome = run_one_dependency_read(
            session_factory, settings=settings, decision=decision,
            owner_id=owner_id, conversation_id=conversation_id,
            turn_id=turn_id, request_id=request_id,
            initial_turn_raw=initial_turn_raw, current_raw=current_raw,
            control=control, step_id=step_id, runtime=runtime)
        results.append(outcome)
        if outcome.status != "checkpointed" or outcome.checkpoint is None:
            return tuple(results)
        current_raw = json.dumps(outcome.checkpoint, ensure_ascii=False)
        if control == "execute_first":
            return tuple(results)
    raise DependencyPlanError("dependency_budget_exceeded")
