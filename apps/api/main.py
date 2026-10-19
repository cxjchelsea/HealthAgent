import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from agent.config import Settings
from agent.providers import make_provider, ModelUnavailable
from agent.understanding import GoalIntake, understand
from agent.decision import NativeToolDecision
from agent.loop import AgentLoop
from services.db import Base, Conversation, Message, Turn, database

log=logging.getLogger("healthagent")
logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')

class ChatIn(BaseModel):
    content: str = Field(min_length=1)
    client_request_id: str = Field(min_length=8,max_length=120)

class GoalIn(BaseModel):
    content: str = Field(min_length=1)

class ConversationOut(BaseModel):
    id: str
    messages: list[dict]

def event(event_type: str, **payload):
    return f"event: {event_type}\ndata: {json.dumps(payload,ensure_ascii=False)}\n\n"

def create_app(settings: Settings | None=None, provider=None, agent_decider=None):
    settings = settings or Settings()
    engine, Session=database(settings.database_url)
    model=provider or make_provider(settings)

    @asynccontextmanager
    async def lifespan(app):
        from services.migrate import migrate
        migrate(settings.database_url)
        yield
        engine.dispose()

    app=FastAPI(title="HealthAgent S0/S1", version="0.1.0",lifespan=lifespan)
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

    @app.post("/agent/runs")
    async def run_agent(body: GoalIn, uid=Depends(user_id)):
        if len(body.content) > settings.max_input_chars:
            raise HTTPException(413, "message_too_long")
        decider = agent_decider if agent_decider is not None else NativeToolDecision(settings)
        return await AgentLoop(decider).run(body.content)

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

    @app.post("/conversations/{cid}/messages")
    async def send(cid:str,body:ChatIn,request:Request,uid=Depends(user_id)):
        if len(body.content)>settings.max_input_chars:
            raise HTTPException(413,"message_too_long")
        with Session.begin() as db:
            get_owned(db,cid,uid)
            prior=db.scalar(select(Turn).where(Turn.conversation_id==cid,Turn.request_id==body.client_request_id))
            if prior:
                raise HTTPException(409,"duplicate_request_id")
            history=db.scalars(select(Message).where(Message.conversation_id==cid,Message.status=="completed")
                               .order_by(Message.created_at,Message.id)).all()
            context=[{"role":m.role,"content":m.content} for m in history[-settings.max_history_messages:]]
            context.append({"role":"user","content":body.content})
            msg=Message(conversation_id=cid,role="user",content=body.content)
            assistant=Message(conversation_id=cid,role="assistant",content="",status="pending")
            db.add_all([msg,assistant])
            db.flush()
            turn=Turn(conversation_id=cid,request_id=body.client_request_id,assistant_message_id=assistant.id,input_chars=len(body.content))
            db.add(turn)
            db.flush()
            message_id=assistant.id
            turn_id=turn.id
        async def produce():
            output = []
            state = "cancelled"
            start = time.monotonic()
            first_delta_ms = None
            delta_count = 0
            stream = None
            yield event("started", conversation_id=cid, message_id=message_id, run_id=turn_id)
            try:
                stream = model.stream(context)
                async for part in stream:
                    if await request.is_disconnected():
                        state = "cancelled"
                        break
                    if not isinstance(part, str):
                        raise ModelUnavailable("Provider emitted invalid content")
                    if not part:
                        continue
                    if first_delta_ms is None:
                        first_delta_ms = int((time.monotonic() - start) * 1000)
                    delta_count += 1
                    output.append(part)
                    yield event("message_delta", message_id=message_id, text=part)
                else:
                    state = "completed"
                if state == "completed":
                    yield event("usage", message_id=message_id,
                                output_chars=len("".join(output)), estimated=True,
                                token_usage_available=False)
                    yield event("message_done", message_id=message_id, status=state)
                else:
                    yield event("message_done", message_id=message_id, status="cancelled")
            except asyncio.CancelledError:
                state = "cancelled"
                raise
            except Exception as exc:
                state = "provider_error"
                log.warning("model failure run=%s class=%s", turn_id, type(exc).__name__)
                yield event("error", message_id=message_id,
                            code="provider_error", detail="Model request failed")
            finally:
                if stream is not None and hasattr(stream, "aclose"):
                    try:
                        await stream.aclose()
                    except Exception:
                        log.warning("provider close failure run=%s", turn_id)
                duration_ms = int((time.monotonic() - start) * 1000)
                with Session.begin() as db:
                    m = db.get(Message, message_id)
                    t = db.get(Turn, turn_id)
                    m.content = "".join(output)
                    m.status = state
                    t.state = state
                    t.output_chars = len(m.content)
                    t.duration_ms = duration_ms
                log.info("run=%s conversation=%s state=%s duration_ms=%s "
                         "first_delta_ms=%s delta_count=%s output_chars=%s",
                         turn_id, cid, state, duration_ms, first_delta_ms,
                         delta_count, len("".join(output)))
        return StreamingResponse(produce(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    @app.get("/",response_class=HTMLResponse)
    def index():
        return (Path(__file__).parents[1]/"web"/"index.html").read_text(encoding="utf-8")
    return app

app=create_app()
