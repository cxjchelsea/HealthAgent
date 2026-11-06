"""Single write fence shared by v1 ordinary and Pending side-effect paths.

On PostgreSQL, the parent conversation row serializes all governed writes.
On SQLite, the database writer transaction serializes commits; the durable
prepared row remains a fail-closed guard. The caller MUST hold an open write
transaction until either (a) it inserts prepared evidence, or (b) atomically
commits the internal Pending effect and its receipt.
"""
from sqlalchemy import and_, or_, select

from services.db import AgentAttempt, Conversation, Turn


WRITE_TOOLS = ("create_task", "save_memory")


def write_fence_in_session(db, conversation_id: str) -> str | None:
    """Lock conversation and check unknown/unfinalized write effects.

    Returns a stable denial code or None. Never reconciles, retries,
    deletes a receipt, or grants permission to dispatch a tool.
    """
    conversation = db.scalar(select(Conversation.id).where(
        Conversation.id == conversation_id).with_for_update())
    if conversation is None:
        return "write_conversation_missing"
    blocked = db.scalar(select(AgentAttempt.id).join(
        Turn, Turn.id == AgentAttempt.turn_id).where(
        AgentAttempt.conversation_id == conversation_id,
        AgentAttempt.tool_name.in_(WRITE_TOOLS),
        or_(
            AgentAttempt.status == "prepared",
            and_(AgentAttempt.status == "succeeded",
                 Turn.state != "completed"),
        )).limit(1))
    if blocked is not None:
        return "write_reconciliation_required"
    return None
