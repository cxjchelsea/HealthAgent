import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from agent.config import Settings
from agent.understanding import GoalIntake, understand
from agent.decision import NativeToolDecision
from agent.loop import AgentLoop
from agent.mock_mcp import MockMCPClient
from agent.context import build_context
from services.task_store import ConversationTaskStore
from services.memory_store import MemoryStore
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
        began = time.monotonic()
        status, answer = "provider_error", ""
        result = None
        try:
            decider = agent_decider if agent_decider is not None else NativeToolDecision(settings)
            result = await AgentLoop(decider, task_store=ConversationTaskStore(Session, cid),
                memory_store=MemoryStore(Session, uid, write_allowed=body.memory_write_consent),
                mcp_client=MockMCPClient()).run(body.content, history=context)
            if result["status"] == "completed":
                answer, status = result["answer"], "completed"
            else:
                status = result["status"]
        except Exception as exc:
            log.warning("conversation Agent failed turn=%s class=%s", turn_id, type(exc).__name__)
            status = "provider_error"
        finally:
            with Session.begin() as db:
                response = db.get(Message, assistant_id)
                turn = db.get(Turn, turn_id)
                if response is not None and turn is not None:
                    response.content = answer
                    response.status = status
                    turn.state = status
                    turn.output_chars = len(answer)
                    turn.duration_ms = int((time.monotonic()-began)*1000)
        if status != "completed":
            raise HTTPException(503, "Agent run did not complete")
        return {**result, "conversation_id": cid, "message_id": assistant_id}

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
