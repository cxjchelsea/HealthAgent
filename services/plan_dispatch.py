"""Durable dispatch before tool call, and bounded result receipt afterward.

A missing receipt means unknown, not safe-to-retry. The host must reconcile
before any further automatic execution. No claims of external exactly-once.
"""
import json
from sqlalchemy import select
from services.db import PlanToolDispatch, Turn
from services.dependency_fencing import assert_dependency_fence_in_session


UNRESOLVED_DISPATCH_STATUSES = ("dispatch_unknown", "observed", "observed_failure")


class DispatchConflict(RuntimeError):
    pass


class PlanDispatchJournal:
    def __init__(self, sessions, conversation_id, turn_id):
        self.sessions, self.conversation_id, self.turn_id = sessions, conversation_id, turn_id

    def begin(self, step_id, name, arguments, *, tool_version=None):
        with self.sessions.begin() as db:
            turn = db.get(Turn, self.turn_id)
            if not turn or turn.conversation_id != self.conversation_id or turn.state != "running":
                raise DispatchConflict("turn_not_running")
            if db.scalar(select(PlanToolDispatch.id).where(
                    PlanToolDispatch.conversation_id == self.conversation_id,
                    PlanToolDispatch.step_id == step_id,
                    PlanToolDispatch.status.in_(UNRESOLVED_DISPATCH_STATUSES))):
                raise DispatchConflict("unreconciled_dispatch")
            entry = PlanToolDispatch(
                conversation_id=self.conversation_id, turn_id=self.turn_id,
                step_id=step_id, tool_name=name,
                tool_version=tool_version,
                arguments_json=json.dumps(arguments, ensure_ascii=False, sort_keys=True),
                status="dispatch_unknown")
            db.add(entry)
            db.flush()
            return entry.id

    def record_receipt(self, dispatch_id, observation, *, fence=None):
        # Store a bounded, non-credential result. Never imply a receipt
        # proves external commit unless the tool protocol guarantees it.
        # Project tool-observed fields BEFORE losing the source observation.
        # Use the same strict allowlist and size budget as S25-03 evidence;
        # legacy receipts with only ok/error/value remain readable.
        from services.evidence_store import bounded_projection
        with self.sessions.begin() as db:
            entry = db.execute(select(PlanToolDispatch).where(
                PlanToolDispatch.id == dispatch_id
            ).with_for_update()).scalar_one_or_none()
            if not entry or entry.turn_id != self.turn_id or entry.status != "dispatch_unknown":
                raise DispatchConflict("dispatch_not_current")
            # Legacy M4 dispatches have no generation and must not inherit
            # an unrelated S25-06 fence. A generation-bound dispatch always
            # requires the exact token even if the old worker was replaced.
            if entry.worker_generation is not None:
                if (fence is None or fence.generation != entry.worker_generation):
                    raise DispatchConflict("dependency_worker_fenced")
                assert_dependency_fence_in_session(
                    db, conversation_id=self.conversation_id,
                    turn_id=self.turn_id, fence=fence)
            elif fence is not None:
                raise DispatchConflict("dependency_worker_fenced")
            bounded = bounded_projection(entry.tool_name, observation)
            # Retain the historical receipt's bounded error field and the
            # string value convention expected by legacy offline recovery.
            bounded["error"] = str(observation.get("error", ""))[:80]
            if bounded["ok"] and "value" in bounded:
                bounded["value"] = str(bounded["value"])[:256]
            entry.receipt_json = json.dumps(bounded, ensure_ascii=False)
            entry.status = "observed" if bounded["ok"] else "observed_failure"

    def checkpoint_in_session(self, db, checkpoint):
        """Only a positively bound completed plan step can close a receipt.

        A success-looking tool result that failed PlanLedger binding must not
        unblock the next request.
        """
        steps = checkpoint.get("steps", []) if isinstance(checkpoint, dict) else []
        for entry in db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.turn_id == self.turn_id,
                PlanToolDispatch.status == "observed")):
            step = next((s for s in steps if s.get("id") == entry.step_id), None)
            if (step is None or step.get("status") != "completed"
                    or step.get("evidence_tool") != entry.tool_name
                    or step.get("expected_arguments") != json.loads(entry.arguments_json)
                    or step.get("tool_version") != entry.tool_version):
                raise DispatchConflict("receipt_checkpoint_binding_mismatch")
            entry.status = "checkpointed"
