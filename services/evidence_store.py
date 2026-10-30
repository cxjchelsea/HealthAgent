"""Bounded S25-03 observation projection, never tool response replay as instructions."""
import hashlib
import json
import uuid

_MAX_RESULT_CHARS = 1200
_ALLOWED_OUTPUT = {
    "calculate": ("value",),
    "utc_time": ("utc",),
    "mcp_reference_lookup": ("source", "definition", "value"),
    "list_tasks": ("tasks",),
    # memory values may be sensitive: presence only, not the fact itself.
    "recall_memory": ("found",),
    "save_memory": (),
    "create_task": ("id",),
}

def bounded_projection(tool_name: str, observation: dict) -> dict:
    """Restrict observation fields and size. Never store credentials or raw tool content."""
    if observation.get("ok") is not True:
        error = str(observation.get("error", "unknown"))
        return {"ok": False, "error": error[:80]}
    allowed = _ALLOWED_OUTPUT.get(tool_name, ())
    data = {"ok": True}
    for key in allowed:
        if key in observation:
            value = observation[key]
            encoded = json.dumps(value, ensure_ascii=False, default=str)
            if len(encoded) <= _MAX_RESULT_CHARS:
                data[key] = value
            else:
                data[key + "_availability"] = "truncated"
    return data

def arguments_digest(arguments: object) -> str:
    raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def make_attempt(run_id: str, tool_name: str, arguments: object, observation: dict, step_id: int | None = None) -> dict:
    projection = bounded_projection(tool_name, observation)
    return {
        "attempt_id": str(uuid.uuid4()), "run_id": run_id,
        "step_id": step_id, "tool_name": tool_name,
        "arguments_digest": arguments_digest(arguments),
        "status": "succeeded" if observation.get("ok") is True else "failed",
        "error_code": None if observation.get("ok") is True else str(observation.get("error", "unknown"))[:80],
        "result_projection": projection,
    }

class AttemptEvidenceStore:
    def __init__(self, conversation_id: str, turn_id: str):
        self.conversation_id = conversation_id
        self.turn_id = turn_id

    def save_in_session(self, db, attempts: list[dict]):
        from services.db import AgentAttempt
        for item in attempts:
            existing = db.get(AgentAttempt, item["attempt_id"])
            if existing is not None:
                if (existing.conversation_id != self.conversation_id or
                    existing.turn_id != self.turn_id or
                    existing.status != item["status"] or
                    existing.arguments_digest != item["arguments_digest"]):
                    raise RuntimeError("attempt_outcome_conflict")
                continue
            db.add(AgentAttempt(
                id=item["attempt_id"], conversation_id=self.conversation_id,
                turn_id=self.turn_id, run_id=item["run_id"],
                step_id=item.get("step_id"), tool_name=item["tool_name"],
                arguments_digest=item["arguments_digest"],
                status=item["status"], error_code=item.get("error_code"),
                result_projection=json.dumps(item["result_projection"], ensure_ascii=False),
            ))

    def begin(self, session_factory, attempt: dict):
        """Commit a prepared attempt BEFORE invoking an external tool.

        An abandoned prepared attempt is NOT proof that the tool never ran.
        Recovery must reconcile it before any non-idempotent retry.
        """
        from services.db import AgentAttempt
        with session_factory.begin() as db:
            db.add(AgentAttempt(
                id=attempt["attempt_id"], conversation_id=self.conversation_id,
                turn_id=self.turn_id, run_id=attempt["run_id"],
                step_id=attempt.get("step_id"), tool_name=attempt["tool_name"],
                arguments_digest=attempt["arguments_digest"], status="prepared",
                result_projection=None,
            ))
            db.flush()

    def finish(self, session_factory, attempt: dict):
        """Persist the observed outcome; cannot overwrite another terminal outcome."""
        from services.db import AgentAttempt
        from sqlalchemy import update
        with session_factory.begin() as db:
            changed = db.execute(update(AgentAttempt).where(
                AgentAttempt.id == attempt["attempt_id"],
                AgentAttempt.conversation_id == self.conversation_id,
                AgentAttempt.turn_id == self.turn_id,
                AgentAttempt.status == "prepared"
            ).values(status=attempt["status"],
                     error_code=attempt.get("error_code"),
                     result_projection=json.dumps(attempt["result_projection"], ensure_ascii=False)))
            if changed.rowcount != 1:
                raise RuntimeError("attempt_outcome_conflict")

    def unresolved(self, session_factory) -> list[dict]:
        """Read-only unknown execution outcomes; never re-dispatch from here."""
        from sqlalchemy import select
        from services.db import AgentAttempt
        with session_factory() as db:
            rows = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == self.conversation_id,
                AgentAttempt.status == "prepared").order_by(
                AgentAttempt.created_at, AgentAttempt.id).limit(100)).all()
            return [{"attempt_id": row.id, "tool": row.tool_name,
                     "status": "unknown_outcome", "step_id": row.step_id}
                    for row in rows]

    def has_unresolved_writes(self, session_factory) -> bool:
        """Database EXISTS, not the first page of an unresolved list."""
        from sqlalchemy import select, and_, or_
        from services.db import AgentAttempt, Turn
        with session_factory() as db:
            return db.scalar(select(AgentAttempt.id).join(
                Turn, Turn.id == AgentAttempt.turn_id).where(
                AgentAttempt.conversation_id == self.conversation_id,
                AgentAttempt.tool_name.in_(("create_task", "save_memory")),
                or_(
                    AgentAttempt.status == "prepared",
                    and_(AgentAttempt.status == "succeeded",
                         Turn.state.not_in(("completed", "partial")))
                )
            ).limit(1)) is not None

    def verified_attempt_refs_for_checkpoint(self, session_factory, checkpoint: dict | None):
        """Return durable success references for this turn and current binding only."""
        if not checkpoint:
            return []
        from sqlalchemy import select
        from services.db import AgentAttempt
        bindings = {step["id"]: (step.get("expected_tool"),
                    arguments_digest(step["expected_arguments"]))
                    for step in checkpoint.get("steps", [])
                    if step.get("status") == "completed"
                    and isinstance(step.get("expected_arguments"), dict)}
        with session_factory() as db:
            rows = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == self.conversation_id,
                AgentAttempt.turn_id == self.turn_id,
                AgentAttempt.status == "succeeded")).all()
            return [{"attempt_id": row.id, "run_id": row.run_id,
                     "step_id": row.step_id, "status": row.status,
                     "arguments_digest": row.arguments_digest}
                    for row in rows if bindings.get(row.step_id) ==
                    (row.tool_name, row.arguments_digest)]

    def reconcile_failed_turn_checkpoint(self, session_factory, checkpoint: dict | None) -> dict | None:
        """Recover ONLY verified, bound pending steps from durable successful attempts.

        Cannot recreate missing plans, infer model-only revisions, or reconcile effects
        whose execution result is unknown.
        """
        if not checkpoint or not isinstance(checkpoint.get("steps"), list):
            return checkpoint
        from sqlalchemy import select
        from services.db import AgentAttempt
        recovered = json.loads(json.dumps(checkpoint))
        with session_factory() as db:
            rows = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == self.conversation_id,
                AgentAttempt.turn_id == self.turn_id,
                AgentAttempt.status == "succeeded",
                AgentAttempt.step_id.is_not(None))).all()
        by_step = {}
        for row in rows:
            if row.step_id in by_step:
                by_step[row.step_id] = None  # Ambiguous; fail closed.
            else:
                by_step[row.step_id] = row
        for step in recovered["steps"]:
            if step.get("status") != "pending":
                continue
            row = by_step.get(step.get("id"))
            if not row or not isinstance(step.get("expected_arguments"), dict):
                continue
            if (row.tool_name == step.get("expected_tool") and
                    row.arguments_digest == arguments_digest(step["expected_arguments"])):
                step["status"] = "completed"
                step["evidence_tool"] = row.tool_name
        return recovered

    def completed_step_projections(self, session_factory, checkpoint: dict | None = None) -> dict[int, dict]:
        """Only previously recorded successful, tool-scoped projections in this conversation."""
        from sqlalchemy import select
        from services.db import AgentAttempt
        with session_factory() as db:
            records = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == self.conversation_id,
                AgentAttempt.status == "succeeded",
                AgentAttempt.step_id.is_not(None)
            ).order_by(AgentAttempt.created_at, AgentAttempt.id)).all()
            accepted = {}
            for step in (checkpoint or {}).get("steps", []):
                if step.get("status") != "completed":
                    continue
                tool = step.get("expected_tool")
                args = step.get("expected_arguments")
                if tool and isinstance(args, dict):
                    digest = hashlib.sha256(json.dumps(args, ensure_ascii=False,
                        sort_keys=True, default=str).encode("utf-8")).hexdigest()
                    accepted[step["id"]] = (tool, digest)
            evidence = {}
            for row in records:
                try:
                    projection = json.loads(row.result_projection or "{}")
                except (TypeError, ValueError):
                    continue
                if projection.get("ok") is True and (checkpoint is None or
                    accepted.get(row.step_id) == (row.tool_name, row.arguments_digest)):
                    evidence[row.step_id] = {"tool": row.tool_name, "result": projection}
            return evidence
