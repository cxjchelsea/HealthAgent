"""Atomic, narrow v1 task creation governed by durable attempt evidence.

Only a Host-admitted, source-bound single create_task may reach this executor.
Task row and success receipt commit in ONE local SQL transaction. A prepared
attempt left by any crash is unknown and must not be replayed automatically.
No claims about external exactly-once or save_memory authority are made.
"""
import json

from sqlalchemy import select, and_, or_
from agent.tools import SCHEMAS
from services.db import AgentAttempt, Turn
from services.evidence_store import (
    AttemptEvidenceStore, bounded_projection, make_attempt,
)
from services.task_store import ConversationTaskStore


def execute_primary_task_create_v1(*, proposal, session_factory,
                                   conversation_id: str, turn_id: str) -> dict:
    trace = [{"step": 0, "type": "primary_write_v1",
              "tool": "create_task"}]

    def deny(reason):
        return {"run_id": turn_id, "status": "partial",
                "termination_reason": reason,
                "answer": "任务写入未通过 Host 执行证据校验，未创建任务。",
                "trace": trace, "steps": 0}

    if (proposal.route_kind != "ordinary_tool"
            or proposal.effect_intent != "mutation_requested"
            or proposal.write_tool_name != "create_task"):
        return deny("primary_v1_write_tool_not_authorized")
    try:
        arguments = SCHEMAS["create_task"].model_validate(
            proposal.write_arguments).model_dump()
    except (ValueError, TypeError):
        return deny("primary_v1_write_arguments_invalid")

    with session_factory() as db:
        # No automatic replay after an unresolved write, including a write
        # whose result was recorded but whose Turn did not complete.
        blocked = db.scalar(select(AgentAttempt.id).join(
            Turn, Turn.id == AgentAttempt.turn_id).where(
            AgentAttempt.conversation_id == conversation_id,
            AgentAttempt.tool_name.in_(("create_task", "save_memory")),
            or_(AgentAttempt.status == "prepared",
                and_(AgentAttempt.status == "succeeded",
                     Turn.state != "completed"))).limit(1))
        if blocked is not None:
            return deny("primary_v1_write_reconciliation_required")

    journal = AttemptEvidenceStore(conversation_id, turn_id)
    attempt = make_attempt(turn_id, "create_task", arguments,
                           {"ok": False, "error": "prepared"})
    journal.begin(session_factory, attempt)
    # After prepared, the only permitted write is the title from Primary's
    # bound request; task and receipt are committed atomically.
    with session_factory.begin() as db:
        turn = db.get(Turn, turn_id)
        record = db.get(AgentAttempt, attempt["attempt_id"])
        if (turn is None or turn.conversation_id != conversation_id
                or turn.state != "running" or record is None
                or record.conversation_id != conversation_id
                or record.turn_id != turn_id or record.status != "prepared"
                or record.tool_name != "create_task"
                or record.arguments_digest != attempt["arguments_digest"]):
            raise RuntimeError("primary_v1_write_claim_conflict")
        observed = ConversationTaskStore(
            session_factory, conversation_id).create_in_session(
                db, arguments["title"])
        record.status = "succeeded" if observed.get("ok") else "failed"
        record.error_code = (
            None if observed.get("ok") else str(
                observed.get("error", "unknown"))[:80])
        record.result_projection = json.dumps(
            bounded_projection("create_task", observed), ensure_ascii=False)

    if observed.get("ok"):
        return {"run_id": turn_id, "status": "completed",
                "termination_reason": "primary_v1_task_created",
                "answer": "已创建任务：" + arguments["title"],
                "trace": trace + [{"step": 1, "type": "tool",
                                   "name": "create_task", "ok": True}],
                "steps": 1}
    return {"run_id": turn_id, "status": "partial",
            "termination_reason": "primary_v1_task_create_failed",
            "answer": "任务未创建，已记录失败结果。",
            "trace": trace + [{"step": 1, "type": "tool",
                               "name": "create_task", "ok": False}],
            "steps": 1}
