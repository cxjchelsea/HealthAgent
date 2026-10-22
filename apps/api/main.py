import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from agent.config import Settings
from agent.understanding import GoalIntake, understand
from agent.decision import NativeToolDecision
from agent.loop import AgentLoop
from agent.mcp_stdio_client import StdioMCPClient
from agent.context import build_context
from services.task_store import ConversationTaskStore
from services.memory_store import MemoryStore
from services.plan_store import ConversationPlanStore
from services.evidence_store import AttemptEvidenceStore
from services.task_state import AgentTaskStateStore
from services.db import AgentAttempt
import json
from services.db import Conversation, Message, Turn, database

log=logging.getLogger("healthagent")
logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')

class ChatIn(BaseModel):
    content: str = Field(min_length=1)
    client_request_id: str = Field(min_length=8,max_length=120)
    memory_write_consent: bool = False

class GoalIn(BaseModel):
    content: str = Field(min_length=1)

class ConversationOut(BaseModel):
    id: str
    messages: list[dict]

def create_app(settings: Settings | None=None, provider=None, agent_decider=None):
    settings = settings or Settings()
    engine, Session=database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        from services.migrate import migrate
        migrate(settings.database_url)
        yield
        engine.dispose()

    app=FastAPI(title="HealthAgent Unified Agent", version="0.2.0",lifespan=lifespan)
    app.state.settings=settings

    def user_id(request:Request):
        tokens=dict(pair.split(":",1) for pair in settings.user_tokens.split(",") if ":" in pair)
        auth=request.headers.get("Authorization","")
        if auth.startswith("Bearer ") and auth[7:] in tokens:
            return tokens[auth[7:]]
        if settings.allow_demo_identity and not tokens:
            return settings.demo_user_id
        raise HTTPException(401,"Authentication required")

    def get_owned(session, cid, uid):
        c=session.get(Conversation,cid)
        if not c or c.owner_id!=uid:
            raise HTTPException(404,"Conversation not found")
        return c

    @app.post("/understanding", response_model=GoalIntake)
    def understand_goal(body: GoalIn, uid=Depends(user_id)):
        return understand(body.content)

    class MemoryInput(BaseModel):
        value: str = Field(min_length=1, max_length=500)

    @app.get("/memories")
    def list_memories(uid=Depends(user_id)):
        return MemoryStore(Session, uid).list()

    @app.put("/memories/{key}")
    def set_memory(key: str, body: MemoryInput, uid=Depends(user_id)):
        if not 1 <= len(key) <= 80:
            raise HTTPException(422, "invalid_memory_key")
        return MemoryStore(Session, uid, write_allowed=True).put(key, body.value)

    @app.delete("/memories/{key}")
    def delete_memory(key: str, uid=Depends(user_id)):
        if not MemoryStore(Session, uid).delete(key):
            raise HTTPException(404, "memory_not_found")
        return {"deleted": True}

    @app.post("/conversations/{cid}/agent/messages")
    async def send_agent_in_conversation(cid: str, body: ChatIn, uid=Depends(user_id)):
        """S2-B1: reuse S1 ownership, persisted turns and completed conversation context."""
        if len(body.content) > settings.max_input_chars:
            raise HTTPException(413, "message_too_long")
        try:
            with Session.begin() as db:
                get_owned(db, cid, uid)
                prior = db.scalar(select(Turn).where(
                    Turn.conversation_id == cid, Turn.request_id == body.client_request_id))
                if prior:
                    raise HTTPException(409, "duplicate_request_id")
                history = db.scalars(select(Message).where(
                    Message.conversation_id == cid, Message.status == "completed")
                    .order_by(Message.created_at, Message.id)).all()
                context = build_context(history, body.content,
                                        max_messages=settings.max_history_messages,
                                        max_chars=settings.context_max_chars)
                user_message = Message(conversation_id=cid, role="user", content=body.content)
                response_message = Message(conversation_id=cid, role="assistant",
                                           content="", status="pending")
                db.add_all([user_message, response_message])
                db.flush()
                turn = Turn(conversation_id=cid, request_id=body.client_request_id,
                            assistant_message_id=response_message.id,
                            input_chars=len(body.content))
                db.add(turn)
                db.flush()
                assistant_id, turn_id = response_message.id, turn.id
        except IntegrityError as exc:
            # A concurrent running turn violates uq_running_turn_per_conversation.
            # Both SQLite and PostgreSQL enforce this atomically at the database.
            with Session() as db:
                active = db.scalar(select(Turn.id).where(
                    Turn.conversation_id == cid, Turn.state == "running"))
            if active:
                raise HTTPException(409, "conversation_turn_in_progress") from exc
            raise
        plan_store = ConversationPlanStore(Session, cid)
        began = time.monotonic()
        status, answer = "provider_error", ""
        result = None
        failure_code = None
        decision_steps = 0
        try:
            existing_checkpoint = plan_store.load()
            prior_task_state = AgentTaskStateStore(cid).load(Session)
            prior_step_results = AttemptEvidenceStore(cid, turn_id).completed_step_projections(Session, existing_checkpoint)
            decider = agent_decider if agent_decider is not None else NativeToolDecision(settings)
            result = await AgentLoop(decider, task_store=ConversationTaskStore(Session, cid),
                memory_store=MemoryStore(Session, uid, write_allowed=body.memory_write_consent),
                mcp_client=StdioMCPClient(), skill_strategy=settings.skill_strategy,
                skill_budget_chars=settings.skill_budget_chars).run(body.content, history=context,
                                      plan_checkpoint=existing_checkpoint,
                                      prior_step_results=prior_step_results)
            if result["status"] in {"completed", "partial"}:
                answer, status = result["answer"], result["status"]
            else:
                status = result["status"]
                failure_code = result.get("failure_code") or status
            decision_steps = result.get("steps", 0)
        except Exception as exc:
            log.warning("conversation Agent failed turn=%s class=%s", turn_id, type(exc).__name__)
            status, answer = "provider_error", ""
            failure_code = "agent_exception"
        # Atomic local DB commit: never expose a successful message separately
        # from its corresponding plan checkpoint and turn terminal state.
        # External tool effects remain outside this transaction.
        try:
            with Session.begin() as db:
                response = db.get(Message, assistant_id)
                turn = db.get(Turn, turn_id)
                if response is None or turn is None or turn.state != "running":
                    raise RuntimeError("turn_finalization_conflict")
                if status in {"completed", "partial"} and result.get("plan_checkpoint"):
                    plan_store.save_in_session(db, result["plan_checkpoint"])
                if status in {"completed", "partial"} and result:
                    AgentTaskStateStore(cid).save_in_session(
                        db, result.get("plan_checkpoint"), status,
                        prior_task_state["revision"] if prior_task_state else None)
                if result and result.get("evidence_attempts"):
                    AttemptEvidenceStore(cid, turn_id).save_in_session(db, result["evidence_attempts"])
                response.content = answer
                response.status = status
                turn.state = status
                turn.output_chars = len(answer)
                turn.duration_ms = int((time.monotonic() - began) * 1000)
                turn.failure_code = failure_code
                turn.decision_steps = decision_steps
        except Exception:
            log.exception("conversation turn finalization failed turn=%s", turn_id)
            raise HTTPException(503, "turn_finalization_failed")
        if status not in {"completed", "partial"}:
            # Only allowlisted metadata; never log messages, arguments or responses.
            safe_trace = [
                {"type": event.get("type"), "step": event.get("step"),
                 "tool": event.get("name"), "ok": event.get("ok")}
                for event in (result or {}).get("trace", [])[-20:]
                if event.get("type") in {"decision", "tool", "step_limit",
                                         "tool_limit", "partial_first_step_verified"}
            ]
            log.warning("Agent turn incomplete turn=%s status=%s reason=%s steps=%s duration_ms=%s trace=%s",
                        turn_id, status, failure_code or status, decision_steps,
                        int((time.monotonic() - began) * 1000), safe_trace)
            raise HTTPException(503, {"code": failure_code or status,
                                      "turn_id": turn_id,
                                      "status": status,
                                      "decision_steps": decision_steps})
        safe_result = {k: v for k, v in result.items() if k != "evidence_attempts"}
        safe_result["attempt_refs"] = [a["attempt_id"] for a in result.get("evidence_attempts", [])]
        return {**safe_result, "conversation_id": cid, "message_id": assistant_id}

    @app.get("/conversations/{cid}/agent/task-state")
    def task_state(cid: str, uid=Depends(user_id)):
        with Session() as db:
            get_owned(db,cid,uid)
        record = AgentTaskStateStore(cid).load(Session)
        return record or {"task_id": None,"revision":0,"plan_revision":0,
                          "state":"not_started","steps":[]}

    @app.get("/conversations/{cid}/turns/{turn_id}/evidence")
    def turn_evidence(cid: str, turn_id: str, uid=Depends(user_id)):
        """Bounded owner-scoped evidence from the actual executor, not model recollection."""
        with Session() as db:
            get_owned(db, cid, uid)
            turn = db.get(Turn, turn_id)
            if turn is None or turn.conversation_id != cid:
                raise HTTPException(404, "Turn not found")
            attempts = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid, AgentAttempt.turn_id == turn_id)
                .order_by(AgentAttempt.created_at, AgentAttempt.id).limit(50)).all()
            return {"turn_id": turn_id, "attempts": [
                {"attempt_id": a.id, "run_id": a.run_id,
                 "step_id": a.step_id, "tool": a.tool_name,
                 "status": a.status, "error_code": a.error_code,
                 "result": json.loads(a.result_projection) if a.result_projection else None}
                for a in attempts]}

    @app.get("/conversations/{cid}/turns/{turn_id}/diagnostics")
    def turn_diagnostics(cid: str, turn_id: str, uid=Depends(user_id)):
        """Owner-scoped bounded metadata, no prompts, messages or credentials."""
        with Session() as db:
            get_owned(db, cid, uid)
            turn = db.get(Turn, turn_id)
            if turn is None or turn.conversation_id != cid:
                raise HTTPException(404, "Turn not found")
            return {"turn_id": turn.id, "status": turn.state,
                    "failure_code": turn.failure_code,
                    "decision_steps": turn.decision_steps,
                    "duration_ms": turn.duration_ms}

    @app.get("/healthz")
    def healthz():return {"status":"ok"}

    @app.get("/readyz")
    def readyz():
        try:
            with engine.connect() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(503,"database_unavailable")
        return {"status":"ready","provider_configured":settings.provider_ready}

    @app.get("/model")
    def model_info(uid=Depends(user_id)):
        return {"provider":settings.model_provider,"model": "fake" if settings.model_provider=="fake" else settings.model_name,
                "configured":settings.provider_ready}

    @app.post("/conversations",status_code=201)
    def new_conversation(uid=Depends(user_id)):
        with Session.begin() as db:
            c=Conversation(owner_id=uid)
            db.add(c)
            db.flush()
            return {"id":c.id}

    @app.get("/conversations")
    def list_conversations(uid=Depends(user_id)):
        with Session() as db:
            rows=db.scalars(select(Conversation).where(Conversation.owner_id==uid).order_by(Conversation.created_at.desc())).all()
            return [{"id":c.id,"created_at":c.created_at.isoformat()} for c in rows]

    @app.get("/conversations/{cid}")
    def get_conversation(cid:str,uid=Depends(user_id)):
        with Session() as db:
            get_owned(db,cid,uid)
            rows=db.scalars(select(Message).where(Message.conversation_id==cid).order_by(Message.created_at,Message.id)).all()
            return {"id":cid,"messages":[{"id":m.id,"role":m.role,"content":m.content,"status":m.status} for m in rows]}

    @app.get("/",response_class=HTMLResponse)
    def index():
        return (Path(__file__).parents[1]/"web"/"index.html").read_text(encoding="utf-8")
    return app

app=create_app()
