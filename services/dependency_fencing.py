"""S25-06: generation-based, non-expiring execution fencing.

No heartbeat timeout is deemed proof of death. Only the locked operator
reconciliation transaction may revoke an active generation.
"""
import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy import select

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    ConversationPlan, DependencyWorkerFence, PlanExecutionClaim, Turn, now,
)


@dataclass(frozen=True)
class DependencyFenceToken:
    conversation_id: str
    turn_id: str
    generation: int
    secret: str


def _deny():
    raise DependencyPlanError("dependency_worker_fenced")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def acquire_dependency_fence(session_factory, *, conversation_id: str,
                             turn_id: str, expected_raw: str) -> DependencyFenceToken:
    """One new token per newly claimed Turn; never reveal an existing token.

    The Plan row is locked before Claim and Fence to match the Host/M4 order.
    """
    secret = secrets.token_urlsafe(32)
    with session_factory.begin() as db:
        plan = db.execute(select(ConversationPlan).where(
            ConversationPlan.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        claim = db.execute(select(PlanExecutionClaim).where(
            PlanExecutionClaim.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        turn = db.get(Turn, turn_id)
        if (plan is None or plan.snapshot != expected_raw
                or claim is None or claim.turn_id != turn_id
                or claim.snapshot != expected_raw
                or turn is None or turn.conversation_id != conversation_id
                or turn.state != "running"):
            _deny()
        existing = db.execute(select(DependencyWorkerFence).where(
            DependencyWorkerFence.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        if existing is None:
            generation = 1
            db.add(DependencyWorkerFence(
                conversation_id=conversation_id, turn_id=turn_id,
                generation=generation, token_digest=_digest(secret),
                state="active", updated_at=now()))
        else:
            # Same turn can never mint a replacement token, including after
            # process death. Another Turn requires prior explicit fencing.
            if existing.state != "fenced" or existing.turn_id == turn_id:
                _deny()
            generation = existing.generation + 1
            existing.turn_id = turn_id
            existing.generation = generation
            existing.token_digest = _digest(secret)
            existing.state = "active"
            existing.updated_at = now()
        db.flush()
    return DependencyFenceToken(conversation_id, turn_id, generation, secret)


def assert_dependency_fence_in_session(db, *, conversation_id: str,
                                       turn_id: str,
                                       fence: DependencyFenceToken | None) -> None:
    """Fail closed for any conversation that has acquired a new-format fence.

    The checks operate under a row lock in the caller's transaction; the
    recovery revocation takes the same lock. Legacy, unfenced M4 flows remain
    unchanged for migration compatibility.
    """
    row = db.execute(select(DependencyWorkerFence).where(
        DependencyWorkerFence.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    if row is None:
        if fence is not None:
            _deny()
        return
    if (type(fence) is not DependencyFenceToken
            or fence.conversation_id != conversation_id
            or fence.turn_id != turn_id
            or fence.generation != row.generation
            or row.turn_id != turn_id
            or row.state != "active"
            or _digest(fence.secret) != row.token_digest):
        _deny()


def revoke_dependency_fence_in_session(db, *, conversation_id: str,
                                       turn_id: str) -> None:
    """Irreversible revocation before releasing the abandoned Claim.

    Legacy recovery records predating the fence migration may have no row.
    Such rows require independent worker-death proof, as before.
    """
    row = db.execute(select(DependencyWorkerFence).where(
        DependencyWorkerFence.conversation_id == conversation_id
    ).with_for_update()).scalar_one_or_none()
    if row is None:
        return
    if row.turn_id != turn_id or row.state != "active":
        _deny()
    row.state = "fenced"
    row.updated_at = now()
