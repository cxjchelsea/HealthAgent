"""Durable per-conversation plan snapshot, owner checked by caller."""
import json

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from services.db import ConversationPlan


class PlanSnapshotConflict(RuntimeError):
    """The durable plan no longer matches the snapshot the caller observed."""


class ConversationPlanStore:
    def __init__(self, session_factory, conversation_id: str):
        self.session_factory = session_factory
        self.conversation_id = conversation_id

    def load(self):
        with self.session_factory() as db:
            record = db.scalar(select(ConversationPlan).where(
                ConversationPlan.conversation_id == self.conversation_id))
            return json.loads(record.snapshot) if record else None

    def load_raw(self):
        """Return the durable serialized snapshot, or None if no row exists."""
        with self.session_factory() as db:
            record = db.get(ConversationPlan, self.conversation_id)
            return record.snapshot if record else None

    def save_in_session(self, db, snapshot: dict):
        record = db.get(ConversationPlan, self.conversation_id)
        if record is None:
            record = ConversationPlan(conversation_id=self.conversation_id)
            db.add(record)
        record.snapshot = json.dumps(snapshot, ensure_ascii=False)

    def compare_and_swap_in_session(self, db, *, expected_raw: str | None,
                                    snapshot: dict) -> None:
        """Atomically replace exactly the observed serialized snapshot.

        expected_raw=None means the row was observed absent. A stale writer
        raises PlanSnapshotConflict instead of silently overwriting a newer plan.
        Caller must keep this transaction boundary appropriate for its workflow.
        """
        replacement = json.dumps(snapshot, ensure_ascii=False)
        if expected_raw is None:
            if db.get(ConversationPlan, self.conversation_id) is not None:
                raise PlanSnapshotConflict("plan_snapshot_conflict")
            db.add(ConversationPlan(conversation_id=self.conversation_id,
                                    snapshot=replacement))
            try:
                db.flush()
            except IntegrityError as exc:
                raise PlanSnapshotConflict("plan_snapshot_conflict") from exc
            return
        result = db.execute(
            update(ConversationPlan)
            .where(ConversationPlan.conversation_id == self.conversation_id,
                   ConversationPlan.snapshot == expected_raw)
            .values(snapshot=replacement)
        )
        if result.rowcount != 1:
            raise PlanSnapshotConflict("plan_snapshot_conflict")

    def save(self, snapshot: dict):
        with self.session_factory.begin() as db:
            self.save_in_session(db, snapshot)
