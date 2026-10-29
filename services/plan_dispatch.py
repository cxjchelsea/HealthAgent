"""Durable dispatch before tool call, and bounded result receipt afterward.

A missing receipt means unknown, not safe-to-retry. The host must reconcile
before any further automatic execution. No claims of external exactly-once.
"""
import json
from sqlalchemy import select
from services.db import PlanToolDispatch, Turn


class DispatchConflict(RuntimeError):
    pass


class PlanDispatchJournal:
    def __init__(self, sessions, conversation_id, turn_id):
        self.sessions, self.conversation_id, self.turn_id = sessions, conversation_id, turn_id

    def begin(self, step_id, name, arguments):
        with self.sessions.begin() as db:
            turn = db.get(Turn, self.turn_id)
            if not turn or turn.conversation_id != self.conversation_id or turn.state != "running":
                raise DispatchConflict("turn_not_running")
            if db.scalar(select(PlanToolDispatch.id).where(
                    PlanToolDispatch.conversation_id == self.conversation_id,
                    PlanToolDispatch.step_id == step_id,
                    PlanToolDispatch.status != "checkpointed")):
                raise DispatchConflict("unreconciled_dispatch")
            entry = PlanToolDispatch(
                conversation_id=self.conversation_id, turn_id=self.turn_id,
                step_id=step_id, tool_name=name,
                arguments_json=json.dumps(arguments, ensure_ascii=False, sort_keys=True),
                status="dispatch_unknown")
            db.add(entry)
            db.flush()
            return entry.id

    def record_receipt(self, dispatch_id, observation):
        # Store a bounded, non-credential result. Never imply a receipt
        # proves external commit unless the tool protocol guarantees it.
        bounded = {"ok": observation.get("ok") is True,
                   "error": str(observation.get("error", ""))[:80]}
        if observation.get("ok") is True and "value" in observation:
            bounded["value"] = str(observation["value"])[:256]
        with self.sessions.begin() as db:
            entry = db.get(PlanToolDispatch, dispatch_id)
            if not entry or entry.turn_id != self.turn_id or entry.status != "dispatch_unknown":
                raise DispatchConflict("dispatch_not_current")
            entry.receipt_json = json.dumps(bounded, ensure_ascii=False)
            entry.status = "observed"

    def checkpoint_in_session(self, db):
        for entry in db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.turn_id == self.turn_id,
                PlanToolDispatch.status == "observed")):
            entry.status = "checkpointed"
