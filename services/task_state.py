"""Versioned task projection and append-only transitions from observed state."""
import json
import uuid
from sqlalchemy import update
from services.db import AgentTaskState, AgentStateTransition, now


def project_task(checkpoint: dict | None, status: str) -> tuple[str, list[dict], int]:
    steps = checkpoint.get("steps", []) if checkpoint else []
    plan_revision = checkpoint.get("revision", 0) if checkpoint else 0
    if status == "completed" and steps and any(s["status"] != "completed" for s in steps):
        status = "partial"
    if status == "completed":
        state = "completed"
    elif status == "partial":
        state = "partial"
    else:
        state = "failed" if status not in ("running", "partial") else status
    summary = [{"id": s["id"], "status": s["status"],
                "evidence_tool": s.get("evidence_tool")} for s in steps]
    return state, summary, plan_revision


class AgentTaskStateStore:
    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id

    def load(self, session_factory):
        with session_factory() as db:
            row = db.get(AgentTaskState, self.conversation_id)
            return None if row is None else {
                "task_id": row.task_id, "revision": row.revision,
                "plan_revision": row.plan_revision, "state": row.state,
                "steps": json.loads(row.step_states)}

    def history(self, session_factory, limit: int = 100):
        from sqlalchemy import select
        with session_factory() as db:
            events = db.scalars(select(AgentStateTransition).where(
                AgentStateTransition.conversation_id == self.conversation_id
            ).order_by(AgentStateTransition.revision, AgentStateTransition.created_at,
                       AgentStateTransition.id).limit(min(max(limit, 1), 100))).all()
            return [{"revision": event.revision, "entity_type": event.entity_type,
                     "step_id": event.step_id, "from_state": event.from_state,
                     "to_state": event.to_state, "reason_code": event.reason_code,
                     "attempt_id": event.attempt_id}
                    for event in events]

    def _transition(self, db, task_id, revision, kind, step_id, before, after, reason, attempt_id=None):
        db.add(AgentStateTransition(
            id=str(uuid.uuid4()), conversation_id=self.conversation_id,
            task_id=task_id, revision=revision, entity_type=kind, step_id=step_id,
            from_state=before, to_state=after, reason_code=reason, attempt_id=attempt_id,
            created_at=now()))

    def save_in_session(self, db, checkpoint: dict | None, status: str,
                        expected_revision: int | None, attempts: list[dict] | None = None):
        state, steps, plan_revision = project_task(checkpoint, status)
        data = json.dumps(steps, ensure_ascii=False)
        # A model- or caller-provided attempt ID is never sufficient evidence.
        # Bind only durable succeeded executor rows matching the current step.
        from services.db import AgentAttempt
        from services.evidence_store import arguments_digest
        successful_attempts = {}
        for attempt in attempts or []:
            step_id = attempt.get("step_id")
            step = next((step for step in steps if step["id"] == step_id), None)
            if not step or step["status"] != "completed":
                continue
            if not step.get("expected_tool") or not isinstance(step.get("expected_arguments"), dict):
                continue
            row = db.get(AgentAttempt, attempt.get("attempt_id"))
            if (row is None or row.conversation_id != self.conversation_id
                    or row.status != "succeeded" or row.step_id != step_id
                    or row.tool_name != step["expected_tool"]
                    or row.arguments_digest != arguments_digest(step["expected_arguments"])
                    or row.run_id != attempt.get("run_id")
                    or row.turn_id != attempt.get("turn_id", row.turn_id)):
                continue
            successful_attempts[step_id] = row.id
        old = db.get(AgentTaskState, self.conversation_id)
        if expected_revision is None:
            if old is not None:
                raise RuntimeError("stale_task_revision")
            task_id = str(uuid.uuid4())
            revision = 1
            row = AgentTaskState(conversation_id=self.conversation_id, task_id=task_id,
                revision=revision, plan_revision=plan_revision, state=state,
                step_states=data, updated_at=now())
            db.add(row)
            db.flush()
            prior_steps = []
            old_state = None
            old_plan_revision = 0
        else:
            if old is None or old.revision != expected_revision:
                raise RuntimeError("stale_task_revision")
            prior_steps = json.loads(old.step_states)
            old_state = old.state
            old_plan_revision = old.plan_revision
            task_id = old.task_id
            revision = expected_revision + 1
            changed = db.execute(update(AgentTaskState).where(
                AgentTaskState.conversation_id == self.conversation_id,
                AgentTaskState.revision == expected_revision
            ).values(revision=revision, plan_revision=plan_revision,
                     state=state, step_states=data, updated_at=now()))
            if changed.rowcount != 1:
                raise RuntimeError("stale_task_revision")
        if old_state != state:
            self._transition(db, task_id, revision, "task", None,
                             old_state, state, "task_status_changed")
        before = {step["id"]: step for step in prior_steps}
        after = {step["id"]: step for step in steps}
        for step_id, step in after.items():
            previous = before.get(step_id)
            previous_status = previous["status"] if previous else None
            if previous_status != step["status"]:
                reason = ("verified_step_completed" if step["status"] == "completed" and step_id in successful_attempts
                          else "legacy_completion_unlinked" if step["status"] == "completed"
                          else "step_state_changed")
                self._transition(db, task_id, revision, "step", step_id,
                                 previous_status, step["status"], reason,
                                 successful_attempts.get(step_id) if step["status"] == "completed" else None)
        if plan_revision > old_plan_revision:
            for step_id, previous in before.items():
                if step_id not in after:
                    self._transition(db, task_id, revision, "step", step_id,
                                     previous["status"], "superseded", "plan_revision_removed_step")
        return task_id, revision
