"""Immutable Primary route evidence written before any authorized dispatch.

This audit record grants no tool permission. Only a deterministic Host decision
can allow effects. No raw messages, quotes, arguments or model output are saved.
"""
import hashlib
import json
import re
import uuid

from sqlalchemy import select

from services.db import (
    ConversationPlan, DependencyRevisionAnchor, Message, PrimaryRouteEvidence, Turn,
)


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
    approved_revision_digest = None
    if (proposal is not None and decision is not None
            and decision.disposition == "allow"
            and decision.reason == "dependency_revision_candidate"):
        if (preturn_plan_raw is None or proposal.dependency_revision_steps is None):
            raise ValueError("primary_revision_evidence_incomplete")
        from agent.dependency_revision_candidate import compile_revision_candidate
        from services.dependency_revision_fingerprint import revision_fingerprint
        with session_factory() as evidence_db:
            current_turn = evidence_db.get(Turn, turn_id)
            current_message = (evidence_db.get(Message, current_turn.user_message_id)
                               if current_turn is not None and current_turn.user_message_id
                               else None)
            if (current_turn is None or current_turn.conversation_id != conversation_id
                    or current_turn.state != "running"
                    or current_message is None
                    or current_message.conversation_id != conversation_id
                    or current_message.role != "user"):
                raise ValueError("primary_revision_evidence_unbound")
            compiled = compile_revision_candidate(
                expected_raw=preturn_plan_raw,
                replacement_pending=[s.model_dump() for s in proposal.dependency_revision_steps],
                current_user_message=current_message.content)
            calculated_digest = revision_fingerprint(compiled.candidate)
            if (decision.approved_revision_digest != calculated_digest):
                raise ValueError("primary_revision_candidate_changed_after_host")
            approved_revision_digest = calculated_digest
    # B3c-1: a resume admission must preserve the EXACT Host-reviewed request
    # in the write-once Turn evidence. A mutable message cannot be used later
    # to reconstruct a missing authorization digest.
    approved_request_digest = None
    if proposal is not None and decision is not None and (
            decision.reason == "dependency_revision_resume_prepare_only"):
        if (decision.disposition != "allow"
                or proposal.route_kind != "plan_resume"
                or proposal.execution_scope != "remaining_steps"
                or proposal.effect_intent != "read_only"
                or preturn_plan_raw is None
                or not isinstance(decision.approved_request_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", decision.approved_request_digest) is None):
            raise ValueError("primary_resume_evidence_incomplete")
        from agent.primary_route_v1 import bind_unique_quote
        from services.dependency_revision_fingerprint import revision_fingerprint
        with session_factory() as evidence_db:
            current_turn = evidence_db.get(Turn, turn_id)
            user = (evidence_db.get(Message, current_turn.user_message_id)
                    if current_turn is not None and current_turn.user_message_id else None)
            plan = evidence_db.get(ConversationPlan, conversation_id)
            anchor = evidence_db.get(DependencyRevisionAnchor, conversation_id)
            if (current_turn is None or current_turn.conversation_id != conversation_id
                    or current_turn.state != "running"
                    or user is None or user.conversation_id != conversation_id
                    or user.role != "user" or user.status != "completed"
                    or not user.content.strip()
                    or plan is None or plan.snapshot != preturn_plan_raw
                    or anchor is None):
                raise ValueError("primary_resume_evidence_unbound")
            try:
                exact = bind_unique_quote(user.content, proposal.evidence.exact_text)
                candidate = json.loads(anchor.approved_candidate_json)
                current = json.loads(preturn_plan_raw)
                origin = json.loads(anchor.origin_refs_json)
                parent = current["lineage"]["parent_snapshot_sha256"]
            except (TypeError, ValueError, KeyError, AttributeError):
                raise ValueError("primary_resume_evidence_unbound") from None
            if (proposal.evidence != exact
                    or hashlib.sha256(user.content.encode("utf-8")).hexdigest()
                        != decision.approved_request_digest
                    or current != candidate  # pristine-only; progressed v2 is B3c-3
                    or current.get("revision") != 2
                    or current.get("plan_executor_version")
                        != "dependency-plan-read-replan-v1"
                    or revision_fingerprint(candidate) != anchor.approved_digest
                    or anchor.parent_snapshot_sha256 != parent
                    or candidate["lineage"]["origin_refs"] != origin):
                raise ValueError("primary_resume_evidence_unbound")
            original = evidence_db.get(PrimaryRouteEvidence, anchor.revision_turn_id)
            if (original is None or original.conversation_id != conversation_id
                    or original.route_kind != "plan_revise"
                    or original.disposition != "allow"
                    or original.admission_reason != "dependency_revision_candidate"
                    or original.approved_revision_digest != anchor.approved_digest):
                raise ValueError("primary_resume_evidence_unbound")
        approved_request_digest = decision.approved_request_digest
    values = {
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "proposal_id": str(uuid.uuid4()),
        "schema_version": proposal.schema_version if proposal else None,
        "route_kind": proposal.route_kind if proposal else None,
        "semantic_attempts": semantic_attempts,
        "preturn_snapshot_digest": snapshot_digest(preturn_plan_raw),
        "approved_revision_digest": approved_revision_digest,
        "approved_request_digest": approved_request_digest,
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
        "approved_revision_digest": row.approved_revision_digest,
        "approved_request_digest": row.approved_request_digest,
        "disposition": row.disposition,
        "admission_reason": row.admission_reason,
    }
