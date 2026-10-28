"""Read-only M3-B pre-execution Turn snapshot; no authorization claims."""
from dataclasses import dataclass
import hashlib
import json
from typing import Any


def _checkpoint_digest(checkpoint: dict[str, Any] | None) -> str | None:
    if not isinstance(checkpoint, dict):
        return None
    canonical = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapturedTurn:
    conversation_id: str
    request_id: str
    turn_id: str
    checkpoint_present: bool
    checkpoint_revision: int | None
    checkpoint_digest: str | None
    pending_action_present: bool
    pending_origin_present: bool


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
        pending_action_present=pending_action is not None,
        pending_origin_present=bool(
            pending_action and clarification_id and pending_question))


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
