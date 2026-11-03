"""Read-only audit for unresolved local write effects.

Never turns observation into new execution authority, deletes evidence,
retries a tool, or reveals memory values.
"""
import json

from sqlalchemy import select

from services.db import AgentAttempt, ConversationTask, PendingWriteAuthority, Turn


def inspect_primary_write_reconciliation(session_factory, conversation_id: str) -> list[dict]:
    """Bounded diagnostic for an operator; not an automatic reconcile action.

    A prepared attempt has UNKNOWN outcome, even when no current task row is
    visible. A succeeded attempt records an atomic local receipt but does not
    establish that the user-facing Turn committed. All records stay untouched.
    """
    with session_factory() as db:
        attempts = db.scalars(select(AgentAttempt).where(
            AgentAttempt.conversation_id == conversation_id,
            AgentAttempt.tool_name.in_(("create_task", "save_memory")),
        ).order_by(AgentAttempt.created_at, AgentAttempt.id).limit(200)).all()
        summary = []
        for item in attempts:
            turn = db.get(Turn, item.turn_id)
            turn_state = turn.state if turn is not None else "missing"
            if item.status == "prepared":
                assessment = "unknown_outcome"
            elif item.status == "succeeded" and turn_state != "completed":
                assessment = "local_receipt_unfinalized"
            elif item.status == "succeeded":
                assessment = "verified_local_receipt"
            elif item.status == "failed":
                assessment = "recorded_failure"
            else:
                assessment = "invalid_attempt_status"
            task_present = None
            if item.tool_name == "create_task" and item.status == "succeeded":
                try:
                    projection = json.loads(item.result_projection or "{}")
                except (TypeError, ValueError):
                    projection = {}
                task_id = projection.get("task_id")
                task = (db.get(ConversationTask, task_id)
                        if isinstance(task_id, str) else None)
                task_present = bool(task is not None
                                    and task.conversation_id == conversation_id)
                if not task_present:
                    assessment = "receipt_effect_conflict"
            summary.append({
                "attempt_id": item.id,
                "turn_id": item.turn_id,
                "tool": item.tool_name,
                "attempt_status": item.status,
                "turn_state": turn_state,
                "assessment": assessment,
                "task_row_present": task_present,
            })
        return summary


def inspect_pending_write_authorities(
        session_factory, conversation_id: str) -> list[dict]:
    """Read-only evidence of permission consumption versus Turn completion.

    A consumed authorization with a running/failed/missing Turn is an
    unfinalized local effect, NOT a permit to execute it again. This never
    changes state, never reports message/argument contents, and does not
    pretend to resolve competing PostgreSQL transactions.
    """
    with session_factory() as db:
        records = db.scalars(select(PendingWriteAuthority).where(
            PendingWriteAuthority.conversation_id == conversation_id
        ).order_by(PendingWriteAuthority.created_at,
                   PendingWriteAuthority.id).limit(200)).all()
        findings = []
        for item in records:
            origin = db.get(Turn, item.origin_turn_id)
            consumed = (db.get(Turn, item.consumed_turn_id)
                        if item.consumed_turn_id else None)
            origin_state = origin.state if origin else "missing"
            consumer_state = (consumed.state if consumed else
                              "missing" if item.consumed_turn_id else None)
            if item.status == "pending" and item.consumed_turn_id is None:
                assessment = "awaiting_user_reply"
            elif item.status == "consumed" and consumer_state == "completed":
                assessment = "consumed_completed"
            elif item.status == "consumed":
                assessment = "consumed_turn_unfinalized"
            else:
                assessment = "authority_state_conflict"
            findings.append({
                "authority_id": item.id,
                "origin_turn_id": item.origin_turn_id,
                "consumed_turn_id": item.consumed_turn_id,
                "origin_state": origin_state,
                "consumer_state": consumer_state,
                "assessment": assessment,
            })
        return findings
