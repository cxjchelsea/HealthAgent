"""Read-only M3-B pre-execution Turn snapshot; no authorization claims."""
from dataclasses import dataclass
import hashlib
import json
from typing import Any


def _digest_json(value: Any) -> str | None:
    if value is None:
        return None
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _checkpoint_digest(checkpoint: dict[str, Any] | None) -> str | None:
    if not isinstance(checkpoint, dict):
        return None
    return _digest_json(checkpoint)


@dataclass(frozen=True)
class CapturedTurn:
    conversation_id: str
    request_id: str
    turn_id: str
    checkpoint_present: bool
    checkpoint_revision: int | None
    checkpoint_digest: str | None
    pending_action: str | None
    clarification_id: str | None
    pending_question_digest: str | None

    @property
    def pending_action_present(self) -> bool:
        return self.pending_action is not None

    @property
    def pending_origin_present(self) -> bool:
        return bool(self.pending_action and self.clarification_id
                    and self.pending_question_digest)


def capture_turn(*, conversation_id: str, request_id: str, turn_id: str,
                 checkpoint: dict[str, Any] | None,
                 pending_action: str | None,
                 clarification_id: str | None,
                 pending_question: str | None) -> CapturedTurn:
    revision = checkpoint.get("revision") if isinstance(checkpoint, dict) else None
    if type(revision) is not int or revision < 1:
        revision = None
    return CapturedTurn(
        conversation_id=conversation_id, request_id=request_id, turn_id=turn_id,
        checkpoint_present=checkpoint is not None,
        checkpoint_revision=revision,
        checkpoint_digest=_checkpoint_digest(checkpoint),
        pending_action=pending_action,
        clarification_id=clarification_id,
        pending_question_digest=_digest_json(pending_question),
    )


def compare_captured_identity(snapshot: CapturedTurn, *,
                              conversation_id: str, request_id: str,
                              turn_id: str) -> dict[str, str | bool | int | None]:
    match = bool(
        snapshot.conversation_id and snapshot.request_id and snapshot.turn_id
        and (snapshot.conversation_id, snapshot.request_id, snapshot.turn_id)
        == (conversation_id, request_id, turn_id))
    return {
        "result": "MATCH" if match else "MISMATCH",
        "checkpoint_present": snapshot.checkpoint_present,
        "checkpoint_revision": snapshot.checkpoint_revision,
        "pending_action_present": snapshot.pending_action_present,
        "pending_origin_present": snapshot.pending_origin_present,
        "authorization_verified": False,
    }


def compare_pending_provenance(snapshot: CapturedTurn, *,
                               pending_action: str | None,
                               clarification_id: str | None,
                               pending_question: str | None
                               ) -> dict[str, str | bool | None]:
    """Compare exact bounded pending-source identity without returning question text."""
    actual_question_digest = _digest_json(pending_question)
    captured_complete = snapshot.pending_origin_present
    actual_complete = bool(pending_action and clarification_id and actual_question_digest)
    if not captured_complete or not actual_complete:
        result = "UNVERIFIABLE"
    elif (snapshot.pending_action, snapshot.clarification_id,
          snapshot.pending_question_digest) == (
              pending_action, clarification_id, actual_question_digest):
        result = "MATCH"
    else:
        result = "MISMATCH"
    return {
        "result": result,
        "captured_action": snapshot.pending_action,
        "captured_clarification_id": snapshot.clarification_id,
        "actual_action": pending_action,
        "actual_clarification_id": clarification_id,
        "authorization_verified": False,
    }


def compare_checkpoint_snapshot(snapshot: CapturedTurn,
                                actual_checkpoint: dict[str, Any] | None
                                ) -> dict[str, str | bool | int | None]:
    """Compare captured pre-turn checkpoint with the snapshot later supplied to legacy routing.

    This detects observable drift; it does not provide locking, CAS, or execution authority.
    """
    actual_digest = _checkpoint_digest(actual_checkpoint)
    actual_present = actual_checkpoint is not None
    if snapshot.checkpoint_present != actual_present:
        result = "CHANGED"
    elif snapshot.checkpoint_digest is None or actual_digest is None:
        result = "UNVERIFIABLE"
    elif snapshot.checkpoint_digest == actual_digest:
        result = "SAME"
    else:
        result = "CHANGED"
    actual_revision = (actual_checkpoint.get("revision")
                       if isinstance(actual_checkpoint, dict) else None)
    if type(actual_revision) is not int or actual_revision < 1:
        actual_revision = None
    return {
        "result": result,
        "captured_revision": snapshot.checkpoint_revision,
        "actual_revision": actual_revision,
        "authorization_verified": False,
    }
