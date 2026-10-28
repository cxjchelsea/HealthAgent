"""Durable per-conversation plan snapshot with CAS and execution reservation."""
import json

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from services.db import ConversationPlan, PlanExecutionClaim, Turn


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

    def _assert_claim_owner(self, db, owner_turn_id: str | None,
                            expected_raw: str | None = None) -> None:
        claim = db.get(PlanExecutionClaim, self.conversation_id)
        if claim is None:
            return
        if (claim.turn_id != owner_turn_id
                or (expected_raw is not None and claim.snapshot != expected_raw)):
            raise PlanSnapshotConflict("plan_execution_claimed")

    def save_in_session(self, db, snapshot: dict):
        # Lock the plan row before checking the claim. All supported durable
        # plan writers use this same row-first coordination boundary.
        record = db.execute(select(ConversationPlan).where(
            ConversationPlan.conversation_id == self.conversation_id
        ).with_for_update()).scalar_one_or_none()
        self._assert_claim_owner(db, None)
        replacement = json.dumps(snapshot, ensure_ascii=False)
        if record is None:
            db.add(ConversationPlan(conversation_id=self.conversation_id,
                                    snapshot=replacement))
        else:
            record.snapshot = replacement

    def compare_and_swap_in_session(self, db, *, expected_raw: str | None,
                                    snapshot: dict,
                                    owner_turn_id: str | None = None) -> None:
        """Replace only the exact observed snapshot, respecting execution claims.

        A saved-plan executor may finalize its own checkpoint by supplying its
        Turn ID. Every other writer is rejected while that Turn owns the claim.
        """
        replacement = json.dumps(snapshot, ensure_ascii=False)
        if expected_raw is None:
            self._assert_claim_owner(db, owner_turn_id)
            if db.get(ConversationPlan, self.conversation_id) is not None:
                raise PlanSnapshotConflict("plan_snapshot_conflict")
            db.add(ConversationPlan(conversation_id=self.conversation_id,
                                    snapshot=replacement))
            try:
                db.flush()
            except IntegrityError as exc:
                raise PlanSnapshotConflict("plan_snapshot_conflict") from exc
            return

        current = db.execute(select(ConversationPlan).where(
            ConversationPlan.conversation_id == self.conversation_id
        ).with_for_update()).scalar_one_or_none()
        self._assert_claim_owner(db, owner_turn_id, expected_raw)
        if current is None or current.snapshot != expected_raw:
            raise PlanSnapshotConflict("plan_snapshot_conflict")
        result = db.execute(
            update(ConversationPlan)
            .where(ConversationPlan.conversation_id == self.conversation_id,
                   ConversationPlan.snapshot == expected_raw)
            .values(snapshot=replacement)
        )
        if result.rowcount != 1:
            raise PlanSnapshotConflict("plan_snapshot_conflict")

    def acquire_execution_claim_in_session(self, db, *, turn_id: str,
                                           expected_raw: str) -> None:
        """Atomically reserve this exact snapshot for a running Turn.

        The plan row is locked first; plan writers take the same lock before
        checking the claim. This makes the claim and CAS share a DB boundary.
        """
        current = db.execute(select(ConversationPlan).where(
            ConversationPlan.conversation_id == self.conversation_id
        ).with_for_update()).scalar_one_or_none()
        turn = db.get(Turn, turn_id)
        if (turn is None or turn.conversation_id != self.conversation_id
                or turn.state != "running" or current is None
                or current.snapshot != expected_raw):
            raise PlanSnapshotConflict("plan_execution_guard_conflict")
        claim = db.get(PlanExecutionClaim, self.conversation_id)
        if claim is not None:
            if claim.turn_id == turn_id and claim.snapshot == expected_raw:
                return
            owner = db.get(Turn, claim.turn_id)
            if owner is not None and owner.state == "running":
                raise PlanSnapshotConflict("plan_execution_claimed")
            # A terminal/missing owner means the reservation is abandoned.
            db.delete(claim)
            db.flush()
        db.add(PlanExecutionClaim(conversation_id=self.conversation_id,
                                 turn_id=turn_id, snapshot=expected_raw))
        try:
            db.flush()
        except IntegrityError as exc:
            raise PlanSnapshotConflict("plan_execution_claimed") from exc

    def release_execution_claim_in_session(self, db, *, turn_id: str) -> None:
        claim = db.get(PlanExecutionClaim, self.conversation_id)
        if claim is not None and claim.turn_id == turn_id:
            db.delete(claim)

    def save(self, snapshot: dict):
        with self.session_factory.begin() as db:
            self.save_in_session(db, snapshot)
