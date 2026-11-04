"""Host-owned, durable one-shot authorization for Pending create_task.

Authority is minted ONLY while finalizing a v1 primary clarification whose
source quote was checked against that same Turn's user message. Consumption
requires the original partial Turn, exact source message digest, same
conversation, matching authority, and a new running Turn. Task, receipt, and
authority status commit atomically in one SQL transaction.

No external exactly-once guarantee is claimed. No prepared side-effect
exists before the internal database transaction. A failure rolls all three
changes back, so the authority stays pending without task creation.
"""
import hashlib
import json
import uuid

from sqlalchemy import select, update
from services.db import (
    AgentAttempt, Message, PendingWriteAuthority, Turn,
)
from services.evidence_store import arguments_digest, bounded_projection
from services.task_store import ConversationTaskStore
from agent.tools import SCHEMAS
from agent.argument_provenance import user_supplied_value
from agent.pending_write_withdrawal_guard import explicitly_withdraws_pending_task_write
from services.write_effect_fence import write_fence_in_session


def source_digest(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def mint_pending_write_authority_in_session(
        db, *, conversation_id: str, origin_turn_id: str,
        origin_user_message_id: str, source_message: str,
        tool_name: str) -> str:
    """Called by the same transaction that finalizes a partial needs_input Turn."""
    if tool_name != "create_task":
        raise ValueError("pending_write_tool_not_supported")
    turn = db.get(Turn, origin_turn_id)
    message = db.get(Message, origin_user_message_id)
    if (turn is None or message is None
            or turn.conversation_id != conversation_id
            or message.conversation_id != conversation_id
            or message.role != "user"
            or message.content != source_message
            or turn.state != "running"):
        raise ValueError("pending_write_origin_not_bound")
    record = PendingWriteAuthority(
        id=str(uuid.uuid4()), conversation_id=conversation_id,
        origin_turn_id=origin_turn_id,
        origin_user_message_id=origin_user_message_id,
        tool_name=tool_name, source_digest=source_digest(source_message),
        status="pending")
    db.add(record)
    db.flush()
    return record.id


def pending_write_authority_for_origin(
        db, *, conversation_id: str, origin_turn: Turn | None,
        pending_action: str | None) -> PendingWriteAuthority | None:
    if (origin_turn is None or origin_turn.state != "partial"
            or origin_turn.failure_code != "needs_input:create_task"
            or pending_action != "create_task"):
        return None
    record = db.scalar(select(PendingWriteAuthority).where(
        PendingWriteAuthority.conversation_id == conversation_id,
        PendingWriteAuthority.origin_turn_id == origin_turn.id,
        PendingWriteAuthority.status == "pending",
        PendingWriteAuthority.tool_name == "create_task"))
    if record is None:
        return None
    source = db.get(Message, record.origin_user_message_id)
    if (source is None or source.conversation_id != conversation_id
            or source.role != "user"
            or source_digest(source.content) != record.source_digest):
        return None
    return record


def consume_pending_task_write_v1(
        *, session_factory, conversation_id: str, current_turn_id: str,
        origin_turn_id: str, authority_id: str, reply: str) -> dict:
    """Single database transaction with CAS consumption, task and receipt."""
    def deny(reason):
        return {"run_id": current_turn_id, "status": "partial",
                "termination_reason": reason,
                "answer": "补充的任务名称未通过原始授权核验，未创建任务。",
                "trace": [{"step": 0, "type": "pending_write_fenced"}],
                "steps": 0}

    if explicitly_withdraws_pending_task_write(reply):
        return deny("pending_write_reply_withdrawn")

    try:
        arguments = SCHEMAS["create_task"].model_validate(
            {"title": reply.strip()}).model_dump()
    except ValueError:
        return deny("pending_write_invalid_title")
    if not user_supplied_value("create_task", arguments, reply):
        return deny("pending_write_title_not_supplied")

    with session_factory.begin() as db:
        current = db.get(Turn, current_turn_id)
        original = db.get(Turn, origin_turn_id)
        authority = db.get(PendingWriteAuthority, authority_id)
        if (current is None or original is None or authority is None
                or current.conversation_id != conversation_id
                or current.state != "running"
                or original.conversation_id != conversation_id
                or original.state != "partial"
                or original.failure_code != "needs_input:create_task"
                or authority.conversation_id != conversation_id
                or authority.origin_turn_id != origin_turn_id
                or authority.tool_name != "create_task"
                or authority.status != "pending"):
            return deny("pending_write_authority_conflict")
        source = db.get(Message, authority.origin_user_message_id)
        if (source is None or source.conversation_id != conversation_id
                or source.role != "user"
                or source_digest(source.content) != authority.source_digest):
            return deny("pending_write_source_changed")
        # Same row lock and unresolved-effect policy as ordinary writes.
        # Keep it held until authority CAS, task and receipt are committed.
        reason = write_fence_in_session(db, conversation_id)
        if reason is not None:
            return deny("pending_write_reconciliation_required")
        changed = db.execute(update(PendingWriteAuthority).where(
            PendingWriteAuthority.id == authority_id,
            PendingWriteAuthority.conversation_id == conversation_id,
            PendingWriteAuthority.origin_turn_id == origin_turn_id,
            PendingWriteAuthority.status == "pending",
            PendingWriteAuthority.consumed_turn_id.is_(None)
        ).values(status="consumed", consumed_turn_id=current_turn_id))
        if changed.rowcount != 1:
            return deny("pending_write_already_consumed")
        observed = ConversationTaskStore(session_factory, conversation_id).create_in_session(
            db, arguments["title"])
        if not observed.get("ok"):
            # The task limit is a deterministic local failure. Roll back
            # authority consumption and report a bounded refusal.
            db.rollback()
            return deny("pending_write_task_capacity_denied")
        db.add(AgentAttempt(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            turn_id=current_turn_id, run_id=current_turn_id,
            tool_name="create_task", arguments_digest=arguments_digest(arguments),
            status="succeeded", error_code=None,
            result_projection=json.dumps(
                bounded_projection("create_task", observed), ensure_ascii=False)))
        db.flush()
    return {
        "run_id": current_turn_id, "status": "completed",
        "termination_reason": "pending_task_created",
        "answer": "已创建任务：" + arguments["title"],
        "trace": [{"step": 0, "type": "pending_write_authority_consumed"},
                  {"step": 1, "type": "tool", "name": "create_task", "ok": True}],
        "steps": 1,
    }

