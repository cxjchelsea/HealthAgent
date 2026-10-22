"""Versioned task-state projection from verified PlanLedger state."""
import json
import uuid
from sqlalchemy import update
from services.db import AgentTaskState, now

def project_task(checkpoint: dict | None, status: str) -> tuple[str, list[dict], int]:
    steps = checkpoint.get("steps", []) if checkpoint else []
    plan_revision = checkpoint.get("revision", 0) if checkpoint else 0
    if status == "completed" and steps and any(s["status"] != "completed" for s in steps):
        status = "partial"
    if status == "completed" and steps and all(s["status"] == "completed" for s in steps):
        state = "completed"
    elif status == "partial":
        state = "partial"
    elif status == "completed" and not steps:
        state = "completed"
    else:
        state = "failed" if status not in ("running", "partial") else status
    summary = [{"id":s["id"],"status":s["status"],
                "evidence_tool":s.get("evidence_tool")} for s in steps]
    return state,summary,plan_revision

class AgentTaskStateStore:
    def __init__(self,conversation_id: str):
        self.conversation_id=conversation_id
    def load(self,session_factory):
        with session_factory() as db:
            row=db.get(AgentTaskState,self.conversation_id)
            return None if row is None else {
                "task_id":row.task_id,"revision":row.revision,
                "plan_revision":row.plan_revision,"state":row.state,
                "steps":json.loads(row.step_states)}
    def save_in_session(self,db,checkpoint:dict|None,status:str,expected_revision:int|None):
        state,steps,plan_revision=project_task(checkpoint,status)
        data=json.dumps(steps,ensure_ascii=False)
        if expected_revision is None:
            if db.get(AgentTaskState,self.conversation_id) is not None:
                raise RuntimeError("stale_task_revision")
            row=AgentTaskState(conversation_id=self.conversation_id,task_id=str(uuid.uuid4()),
                revision=1,plan_revision=plan_revision,state=state,step_states=data,
                updated_at=now())
            db.add(row)
            db.flush()
            return row.task_id,1
        changed=db.execute(update(AgentTaskState).where(
            AgentTaskState.conversation_id==self.conversation_id,
            AgentTaskState.revision==expected_revision).values(
                revision=expected_revision+1,plan_revision=plan_revision,
                state=state,step_states=data,updated_at=now()))
        if changed.rowcount!=1:
            raise RuntimeError("stale_task_revision")
        row=db.get(AgentTaskState,self.conversation_id)
        return row.task_id,expected_revision+1
