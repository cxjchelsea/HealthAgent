"""Read-only physical inspection of an unfinished revision2 second Turn.

Unlike the S25-06 v1 recovery API this has NO operator-release action.
It does not claim an orphan exists, that a worker died, or that an unknown
read can be repeated. It never infers safe retry from an absent Receipt.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    ConversationPlan, DependencyWorkerFence, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn, Message,
)
from services.dependency_revision_progress import (
    _verify_revised_checkpoint_in_session,
)
from services.evidence_store import bounded_projection
from services.primary_route_evidence import snapshot_digest


@dataclass(frozen=True)
class RevisedSecondTurnInspection:
    conversation_id: str
    turn_id: str
    dispatch_id: str
    classification: str
    plan_sha256: str
    evidence_sha256: str


def _deny():
    raise DependencyPlanError("dependency_v2_second_recovery_untrusted")


def inspect_revised_second_turn(
    session_factory, *, owner_id: str, conversation_id: str, turn_id: str,
    expected_raw: str,
) -> RevisedSecondTurnInspection:
    """DB-locked inspection. Never recovers, releases or retries anything."""
    if not all(type(x) is str and x.strip() for x in (
            owner_id, conversation_id, turn_id, expected_raw)):
        _deny()
    with session_factory.begin() as db:
        saved = db.execute(select(ConversationPlan).where(
            ConversationPlan.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        claim = db.execute(select(PlanExecutionClaim).where(
            PlanExecutionClaim.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        fence = db.execute(select(DependencyWorkerFence).where(
            DependencyWorkerFence.conversation_id == conversation_id
        ).with_for_update()).scalar_one_or_none()
        turn = db.get(Turn, turn_id)
        host = db.get(PrimaryRouteEvidence, turn_id)
        user = (db.get(Message, turn.user_message_id)
                if turn and turn.user_message_id else None)
        if (saved is None or saved.snapshot != expected_raw
                or claim is None or claim.snapshot != expected_raw
                or claim.turn_id != turn_id or fence is None
                or fence.turn_id != turn_id or fence.state != "active"
                or turn is None or turn.conversation_id != conversation_id
                or turn.state != "running" or turn.failure_code is not None
                or turn.assistant_message_id is not None
                or host is None or host.conversation_id != conversation_id
                or host.disposition != "allow"
                or host.route_kind != "plan_resume"
                or host.schema_version != "primary-route-v1"
                or host.admission_reason != "dependency_revision_resume_prepare_only"
                or user is None or user.conversation_id != conversation_id
                or user.role != "user" or user.status != "completed"
                or not user.content.strip()
                or host.approved_request_digest
                   != hashlib.sha256(user.content.encode("utf-8")).hexdigest()
                or host.preturn_snapshot_digest != snapshot_digest(expected_raw)):
            _deny()
        pending = db.scalars(select(PlanToolDispatch).where(
            PlanToolDispatch.conversation_id == conversation_id,
            PlanToolDispatch.turn_id == turn_id
        ).with_for_update()).all()
        if (len(pending) != 1 or pending[0].plan_revision != 2
                or pending[0].tool_name != "text_stats"
                or pending[0].status not in (
                    "dispatch_unknown", "invoking", "observed",
                    "observed_failure")):
            _deny()
        entry = pending[0]
        _verify_revised_checkpoint_in_session(
            db, owner_id=owner_id, conversation_id=conversation_id,
            expected_raw=expected_raw, continuation_turn_id=turn_id,
            pending_dispatch_id=entry.id)
        try:
            args = json.loads(entry.arguments_json)
            if type(args) is not dict or type(args.get("text")) is not str:
                _deny()
        except (TypeError, ValueError):
            _deny()
        if entry.status in ("dispatch_unknown", "invoking"):
            if entry.receipt_json is not None:
                _deny()
            # Dispatch unknown might have been invoked by another legacy
            # caller; neither classification grants a retry.
            classification = ("reserved_no_safe_replay" if
                              entry.status == "dispatch_unknown"
                              else "uncertain_no_replay")
        else:
            try:
                receipt = json.loads(entry.receipt_json)
            except (TypeError, ValueError):
                _deny()
            if (type(receipt) is not dict
                    or type(receipt.get("ok")) is not bool
                    or bounded_projection("text_stats", receipt) != receipt
                    or receipt["ok"] != (entry.status == "observed")):
                _deny()
            if receipt["ok"]:
                if (type(receipt.get("characters")) is not int
                        or type(receipt.get("words")) is not int
                        or receipt["characters"] != len(args["text"])
                        or receipt["words"] != len(args["text"].split())):
                    _deny()
                classification = "verified_receipt_checkpoint_required"
            else:
                classification = "verified_failure_no_replay"
        facts = {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "dispatch_id": entry.id,
            "status": entry.status,
            "receipt": entry.receipt_json,
            "arguments_sha256": hashlib.sha256(
                entry.arguments_json.encode("utf-8")).hexdigest(),
            "worker_generation": fence.generation,
            "worker_token_digest": fence.token_digest,
            "snapshot_sha256": hashlib.sha256(
                expected_raw.encode("utf-8")).hexdigest(),
            "claim_turn_id": claim.turn_id,
        }
        evidence = hashlib.sha256(json.dumps(
            facts, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        return RevisedSecondTurnInspection(
            conversation_id=conversation_id, turn_id=turn_id,
            dispatch_id=entry.id, classification=classification,
            plan_sha256=facts["snapshot_sha256"],
            evidence_sha256=evidence)
