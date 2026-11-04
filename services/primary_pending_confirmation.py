"""Explicit two-step Pending create_task consent (Primary Unified only).

The model may propose a candidate; it never confirms a write.  Confirmation
requires a separate authenticated Host endpoint, exact minted nonce, active
candidate and original source binding. The task, attempt, authority CAS and
confirmation Turn are committed by one database transaction.
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from agent.tools import SCHEMAS
from agent.pending_write_withdrawal_guard import (
    explicitly_withdraws_pending_task_write, standalone_pending_task_title,
)
from services.db import (
    AgentAttempt, Message, PendingWriteAuthority, Turn, Conversation,
)
from services.evidence_store import arguments_digest, bounded_projection
from services.primary_pending_write import source_digest
from services.task_store import ConversationTaskStore
from services.write_effect_fence import write_fence_in_session


CANDIDATE_TTL = timedelta(minutes=15)


def _expired(value):
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= datetime.now(timezone.utc)


def _lock_conversation(db, cid):
    return db.scalar(select(Conversation.id).where(
        Conversation.id == cid).with_for_update()) is not None


def _digest(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def stage_pending_task_candidate(
        *, session_factory, conversation_id: str, current_turn_id: str,
        origin_turn_id: str, authority_id: str, reply: str) -> dict:
    """Stage candidate only. No AgentAttempt/task is created here."""
    def deny(code):
        return {"run_id": current_turn_id, "status": "partial",
                "termination_reason": code,
                "answer": "待办标题尚未通过候选校验，未创建任务。",
                "trace": [{"step": 0, "type": "pending_confirmation_fenced"}],
                "steps": 0}

    if explicitly_withdraws_pending_task_write(reply):
        return deny("pending_write_reply_withdrawn")
    try:
        title = SCHEMAS["create_task"].model_validate(
            {"title": reply.strip()}).title
    except ValueError:
        return deny("pending_write_invalid_title")
    if not standalone_pending_task_title(title):
        return deny("pending_write_title_not_standalone")
    nonce = str(uuid.uuid4())
    expiry = datetime.now(timezone.utc) + CANDIDATE_TTL
    with session_factory.begin() as db:
        if not _lock_conversation(db, conversation_id):
            return deny("pending_confirmation_conversation_missing")
        current = db.get(Turn, current_turn_id)
        origin = db.get(Turn, origin_turn_id)
        auth = db.get(PendingWriteAuthority, authority_id)
        if (current is None or origin is None or auth is None
                or current.state != "running"
                or current.conversation_id != conversation_id
                or origin.conversation_id != conversation_id
                or origin.state != "partial"
                or origin.failure_code != "needs_input:create_task"
                or auth.conversation_id != conversation_id
                or auth.origin_turn_id != origin_turn_id
                or auth.tool_name != "create_task"
                or auth.status != "pending"
                or auth.consumed_turn_id is not None):
            return deny("pending_confirmation_origin_conflict")
        source = db.get(Message, auth.origin_user_message_id)
        if (source is None or source.conversation_id != conversation_id
                or source.role != "user"
                or source_digest(source.content) != auth.source_digest):
            return deny("pending_confirmation_source_changed")
        changed = db.execute(update(PendingWriteAuthority).where(
            PendingWriteAuthority.id == authority_id,
            PendingWriteAuthority.conversation_id == conversation_id,
            PendingWriteAuthority.status == "pending",
            PendingWriteAuthority.consumed_turn_id.is_(None),
        ).values(
            status="awaiting_confirmation", candidate_turn_id=current_turn_id,
            candidate_title=title, candidate_digest=_digest(title),
            confirmation_nonce=nonce, candidate_expires_at=expiry))
        if changed.rowcount != 1:
            return deny("pending_confirmation_stage_conflict")
    return {
        "run_id": current_turn_id, "status": "partial",
        "termination_reason": "pending_write_confirmation_required",
        "answer": "拟创建待办：" + title + "。请使用确认操作提交；当前没有创建任务。",
        "confirmation": {
            "authority_id": authority_id, "confirmation_nonce": nonce,
            "candidate_digest": _digest(title), "candidate_title": title,
        },
        "trace": [{"step": 0, "type": "pending_candidate_staged"}],
        "steps": 0,
    }


def commit_confirmed_pending_task(
        *, session_factory, conversation_id: str, authority_id: str,
        confirmation_nonce: str, candidate_digest: str,
        request_id: str) -> dict:
    """Host action: no model classification; single atomic task commit."""
    with session_factory.begin() as db:
        if not _lock_conversation(db, conversation_id):
            return {"status": "denied", "reason": "confirmation_conversation_missing"}
        previous = db.scalar(select(Turn).where(
            Turn.conversation_id == conversation_id,
            Turn.request_id == request_id))
        if previous is not None:
            return {"status": "denied", "reason": "duplicate_request_id"}
        auth = db.get(PendingWriteAuthority, authority_id)
        if (auth is None or auth.conversation_id != conversation_id
                or auth.tool_name != "create_task"
                or auth.status != "awaiting_confirmation"
                or auth.consumed_turn_id is not None
                or auth.confirmation_nonce != confirmation_nonce
                or auth.candidate_digest != candidate_digest
                or not auth.candidate_title
                or _digest(auth.candidate_title) != candidate_digest):
            return {"status": "denied", "reason": "confirmation_binding_conflict"}
        if _expired(auth.candidate_expires_at):
            db.execute(update(PendingWriteAuthority).where(
                PendingWriteAuthority.id == authority_id,
                PendingWriteAuthority.status == "awaiting_confirmation"
            ).values(status="expired"))
            return {"status": "denied", "reason": "confirmation_expired"}
        origin = db.get(Turn, auth.origin_turn_id)
        candidate = db.get(Turn, auth.candidate_turn_id)
        source = db.get(Message, auth.origin_user_message_id)
        if (origin is None or candidate is None or source is None
                or origin.conversation_id != conversation_id
                or candidate.conversation_id != conversation_id
                or source.conversation_id != conversation_id
                or origin.state != "partial"
                or origin.failure_code != "needs_input:create_task"
                or candidate.state != "partial"
                or candidate.failure_code != "pending_write_confirmation_required"
                or source.role != "user"
                or source_digest(source.content) != auth.source_digest):
            return {"status": "denied", "reason": "confirmation_origin_untrusted"}
        # The candidate Turn must still be the LATEST Turn. A later user
        # request supersedes candidate consent; UUID order is not chronology.
        latest_turn_id = db.scalar(select(Turn.id).join(
            Message, Turn.assistant_message_id == Message.id).where(
            Turn.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(1))
        if latest_turn_id != candidate.id:
            return {"status": "denied", "reason": "confirmation_superseded"}
        fence = write_fence_in_session(db, conversation_id)
        if fence is not None:
            return {"status": "denied", "reason": "confirmation_reconciliation_required"}
        changed = db.execute(update(PendingWriteAuthority).where(
            PendingWriteAuthority.id == authority_id,
            PendingWriteAuthority.conversation_id == conversation_id,
            PendingWriteAuthority.status == "awaiting_confirmation",
            PendingWriteAuthority.consumed_turn_id.is_(None),
            PendingWriteAuthority.confirmation_nonce == confirmation_nonce,
            PendingWriteAuthority.candidate_digest == candidate_digest,
            PendingWriteAuthority.candidate_expires_at > datetime.now(timezone.utc),
        ).values(status="consumed"))
        if changed.rowcount != 1:
            return {"status": "denied", "reason": "confirmation_already_used"}
        title = auth.candidate_title
        user_message = Message(
            conversation_id=conversation_id, role="user",
            content="[structured_pending_write_confirmation]",
            status="completed")
        assistant = Message(
            conversation_id=conversation_id, role="assistant",
            content="已确认创建待办：" + title, status="completed")
        db.add_all([user_message, assistant])
        db.flush()
        turn = Turn(
            conversation_id=conversation_id, request_id=request_id,
            state="completed", assistant_message_id=assistant.id,
            input_chars=0, output_chars=len(assistant.content),
            duration_ms=0, decision_steps=1)
        db.add(turn)
        db.flush()
        auth.consumed_turn_id = turn.id
        observed = ConversationTaskStore(session_factory, conversation_id).create_in_session(
            db, title)
        if not observed.get("ok"):
            db.rollback()
            return {"status": "denied", "reason": "confirmation_task_capacity_denied"}
        args = {"title": title}
        db.add(AgentAttempt(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            turn_id=turn.id, run_id=turn.id, tool_name="create_task",
            arguments_digest=arguments_digest(args),
            status="succeeded", error_code=None,
            result_projection=json.dumps(
                bounded_projection("create_task", observed), ensure_ascii=False)))
        db.flush()
        return {"status": "completed",
                "termination_reason": "pending_confirmed_task_created",
                "turn_id": turn.id, "task_id": observed["task_id"]}


def reject_pending_task_candidate(
        *, session_factory, conversation_id: str, authority_id: str,
        confirmation_nonce: str, candidate_digest: str) -> dict:
    """Structured cancellation, no model call and no write effect."""
    with session_factory.begin() as db:
        if not _lock_conversation(db, conversation_id):
            return {"status": "denied", "reason": "confirmation_conversation_missing"}
        changed = db.execute(update(PendingWriteAuthority).where(
            PendingWriteAuthority.id == authority_id,
            PendingWriteAuthority.conversation_id == conversation_id,
            PendingWriteAuthority.tool_name == "create_task",
            PendingWriteAuthority.status == "awaiting_confirmation",
            PendingWriteAuthority.consumed_turn_id.is_(None),
            PendingWriteAuthority.confirmation_nonce == confirmation_nonce,
            PendingWriteAuthority.candidate_digest == candidate_digest,
        ).values(status="revoked"))
        if changed.rowcount != 1:
            return {"status": "denied", "reason": "confirmation_binding_conflict"}
        return {"status": "revoked", "reason": "pending_candidate_rejected"}
