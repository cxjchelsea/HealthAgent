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

def make_attempt(run_id: str, tool_name: str, arguments: object, observation: dict, step_id: int | None = None) -> dict:
    raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    projection = bounded_projection(tool_name, observation)
    return {
        "attempt_id": str(uuid.uuid4()), "run_id": run_id,
        "step_id": step_id, "tool_name": tool_name,
        "arguments_digest": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
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
            db.add(AgentAttempt(
                id=item["attempt_id"], conversation_id=self.conversation_id,
                turn_id=self.turn_id, run_id=item["run_id"],
                step_id=item.get("step_id"), tool_name=item["tool_name"],
                arguments_digest=item["arguments_digest"],
                status=item["status"], error_code=item.get("error_code"),
                result_projection=json.dumps(item["result_projection"], ensure_ascii=False),
            ))
