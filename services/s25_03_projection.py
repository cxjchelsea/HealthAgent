"""S25-03: read-only execution evidence projected from M4 durable dispatch.

The M4 journal/Host and plan checkpoint remain the ONLY source of execution
authority. This module never begins, retries, or authorizes a tool call.
Projection and task transitions are written in the same finalization tx.
"""
import json

from sqlalchemy import select

from services.db import AgentAttempt, AgentTaskState, PlanToolDispatch
from services.evidence_store import arguments_digest, bounded_projection
from services.task_state import AgentTaskStateStore


def project_finalized_turn(db, *, conversation_id: str, turn_id: str,
                           result: dict | None, status: str,
                           checkpoint: dict | None,
                           unknown_execution: bool = False) -> None:
    """Persist bounded observed state and strictly bound execution receipts.

    A checkpointed M4 receipt is stronger than a trace/model claim: M4 has
    already verified plan step, tool name, arguments and CAS at this point.
    Without such a receipt there is no verified AgentAttempt success.
    """
    result = result or {}
    allowed_steps = {
        item["id"]: item for item in (checkpoint or {}).get("steps", [])
        if isinstance(item, dict) and item.get("status") == "completed"
    }
    refs = []
    dispatches = db.scalars(select(PlanToolDispatch).where(
        PlanToolDispatch.conversation_id == conversation_id,
        PlanToolDispatch.turn_id == turn_id).order_by(PlanToolDispatch.created_at,
                                                     PlanToolDispatch.id)).all()
    for dispatch in dispatches:
        if dispatch.status != "checkpointed":
            continue
        step = allowed_steps.get(dispatch.step_id)
        if not step or step.get("expected_tool") != dispatch.tool_name:
            raise RuntimeError("s25_projection_missing_checkpoint_binding")
        try:
            args = json.loads(dispatch.arguments_json)
            receipt = json.loads(dispatch.receipt_json or "{}")
        except (ValueError, TypeError) as exc:
            raise RuntimeError("s25_projection_invalid_dispatch_receipt") from exc
        if (not isinstance(args, dict)
                or args != step.get("expected_arguments")
                or receipt.get("ok") is not True
                or step.get("evidence_tool") != dispatch.tool_name):
            raise RuntimeError("s25_projection_unverified_execution")
        if db.get(AgentAttempt, dispatch.id) is not None:
            raise RuntimeError("s25_projection_duplicate_dispatch")
        run_id = result.get("run_id") or turn_id
        db.add(AgentAttempt(
            id=dispatch.id, conversation_id=conversation_id, turn_id=turn_id,
            run_id=run_id, step_id=dispatch.step_id, tool_name=dispatch.tool_name,
            arguments_digest=arguments_digest(args), status="succeeded",
            # Never expose the M4 raw receipt in the S25-03 evidence API.
            # Project from the actual persisted receipt using the same
            # per-tool field allowlist and size bounds as ordinary tools.
            result_projection=json.dumps(
                bounded_projection(dispatch.tool_name, receipt),
                ensure_ascii=False),
        ))
        refs.append({"attempt_id": dispatch.id, "run_id": run_id,
                     "turn_id": turn_id, "step_id": dispatch.step_id})
    if any(d.status in {"dispatch_unknown", "observed", "observed_failure"}
           for d in dispatches):
        unknown_execution = True
    db.flush()
    old = db.get(AgentTaskState, conversation_id)
    if checkpoint is None and old is None:
        return
    effective_status = ("blocked" if unknown_execution else
                        "interrupted" if status not in {"completed", "partial"}
                        else status)
    AgentTaskStateStore(conversation_id).save_in_session(
        db, checkpoint, effective_status,
        old.revision if old is not None else None,
        refs, current_turn_id=turn_id)
