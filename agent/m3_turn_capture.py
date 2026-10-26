"""Read-only M3-B pre-execution identity snapshot; no authorization claims."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapturedTurn:
    conversation_id: str
    request_id: str
    turn_id: str
    checkpoint_present: bool
    checkpoint_revision: int | None
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
