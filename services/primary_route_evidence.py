"""Immutable Primary route evidence written before any authorized dispatch.

This audit record grants no tool permission. Only a deterministic Host decision
can allow effects. No raw messages, quotes, arguments or model output are saved.
"""
import hashlib
import uuid

from sqlalchemy import select

from services.db import PrimaryRouteEvidence, Turn


def snapshot_digest(raw: str | None) -> str:
    # Distinguish a missing snapshot from an empty serialized snapshot.
    payload = b"absent:" if raw is None else b"present:" + raw.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_primary_admission(*, session_factory, conversation_id: str,
                             turn_id: str, preturn_plan_raw: str | None,
                             proposal=None, decision=None,
                             semantic_attempts: int = 0,
                             refusal_reason: str | None = None) -> str:
    """Commit exactly one proposal+Host result for a running, owned Turn.

    An existing row is never replaced, even after a crash, retry or duplicate
    request. A rejected/invalid proposal is explicitly recorded as refused.
    """
    if (type(semantic_attempts) is not int or semantic_attempts < 0
            or semantic_attempts > 1000):
        raise ValueError("primary_evidence_invalid_attempts")
    if (proposal is None) != (decision is None):
        raise ValueError("primary_evidence_incomplete_decision")
    if proposal is None and not refusal_reason:
        raise ValueError("primary_evidence_missing_refusal")
    if proposal is not None and (
            decision.route_kind != proposal.route_kind
            or decision.disposition not in {"allow", "deny", "restrict", "clarify"}):
        raise ValueError("primary_evidence_host_mismatch")
    values = {
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "proposal_id": str(uuid.uuid4()),
        "schema_version": proposal.schema_version if proposal else None,
        "route_kind": proposal.route_kind if proposal else None,
        "semantic_attempts": semantic_attempts,
        "preturn_snapshot_digest": snapshot_digest(preturn_plan_raw),
        "disposition": decision.disposition if decision else "refused",
        "admission_reason": (decision.reason if decision else refusal_reason),
    }
    if len(values["admission_reason"]) > 80:
        raise ValueError("primary_evidence_reason_not_bounded")
    with session_factory.begin() as db:
        turn = db.get(Turn, turn_id)
        if (turn is None or turn.conversation_id != conversation_id
                or turn.state != "running"):
            raise RuntimeError("primary_evidence_turn_not_running")
        if db.scalar(select(PrimaryRouteEvidence.turn_id).where(
                PrimaryRouteEvidence.turn_id == turn_id)) is not None:
            raise RuntimeError("primary_evidence_already_recorded")
        db.add(PrimaryRouteEvidence(**values))
        db.flush()
    return values["proposal_id"]


def read_primary_evidence(db, *, conversation_id: str, turn_id: str):
    row = db.get(PrimaryRouteEvidence, turn_id)
    if row is None or row.conversation_id != conversation_id:
        return None
    return {
        "turn_id": row.turn_id, "proposal_id": row.proposal_id,
        "schema_version": row.schema_version, "route_kind": row.route_kind,
        "semantic_attempts": row.semantic_attempts,
        "preturn_snapshot_digest": row.preturn_snapshot_digest,
        "disposition": row.disposition,
        "admission_reason": row.admission_reason,
    }
