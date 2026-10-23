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
from agent.understanding import UnderstandingContext, UnderstandingService, HTTPUnderstandingModel, UnderstandingError
from agent.execution_policy import decide_execution_policy_detail, verify_text_only_policy, verify_execute_policy
from agent.decision import NativeToolDecision
from agent.loop import AgentLoop
from agent.tools import execute_tool, SCHEMAS
from agent.argument_provenance import REQUIRED_USER_VALUES
from agent.pending_reply import classify_pending_reply
from agent.mcp_stdio_client import StdioMCPClient
from agent.context import build_context
from services.task_store import ConversationTaskStore
from services.memory_store import MemoryStore
from services.plan_store import ConversationPlanStore
from services.goal_store import ConversationGoalStore
from services.db import Conversation, Message, Turn, database

log=logging.getLogger("healthagent")
logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')

class ChatIn(BaseModel):
    content: str = Field(min_length=1)
    client_request_id: str = Field(min_length=8,max_length=120)
    memory_write_consent: bool = False
    goal_confirmation: str | None = None
    expected_goal_version: int | None = None

class GoalIn(BaseModel):
    content: str = Field(min_length=1)
    conversation_id: str | None = None

class ConversationOut(BaseModel):
    id: str
    messages: list[dict]

def create_app(settings: Settings | None=None, provider=None, agent_decider=None, execution_policy=None):
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

    @app.post("/understanding")
    async def understand_goal(body: GoalIn, uid=Depends(user_id)):
        """S25-01: one typed understanding service, never a rule-based classifier."""
        if settings.model_provider == "fake":
            raise HTTPException(503, {"code": "understanding_requires_real_model"})
        service = UnderstandingService(
            HTTPUnderstandingModel(settings.model_provider, settings.model_base_url,
                                   settings.model_api_key, settings.model_timeout_seconds,
                                   settings.model_max_output_tokens),
            settings.understanding_light_model, settings.understanding_strong_model,
                    semantic_review=True)
        try:
            history = []
            plan_summary = []
            if body.conversation_id is not None:
                with Session() as db:
                    get_owned(db, body.conversation_id, uid)
                    rows = db.scalars(select(Message).where(
                        Message.conversation_id == body.conversation_id,
                        Message.status == "completed"
                    ).order_by(Message.created_at, Message.id)).all()
                    history = build_context(rows, body.content,
                        max_messages=settings.max_history_messages,
                        max_chars=settings.context_max_chars)
                snapshot = ConversationPlanStore(Session, body.conversation_id).load()
                if snapshot:
                    plan_summary = [
                        {"id": step.get("id"), "goal": step.get("goal"),
                         "status": step.get("status"), "revision": snapshot.get("revision")}
                        for step in snapshot.get("steps", [])[:8]
                    ]
            outcome = await service.understand(UnderstandingContext(
                current_message=body.content, recent_completed_messages=history,
                current_plan_summary=plan_summary))
            return {"understanding": outcome["result"].model_dump(),
                    "model_role": outcome["model_role"], "model": outcome["model"],
                    "duration_ms": outcome["duration_ms"], "escalated": outcome["escalated"],
                    "routing_attempts": outcome["attempts"]}
        except UnderstandingError as exc:
            raise HTTPException(503, {"code": exc.code}) from exc

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
                latest_turn = db.scalar(select(Turn).join(
                    Message, Message.id == Turn.assistant_message_id).where(
                    Turn.conversation_id == cid, Turn.state != "running")
                    .order_by(Message.created_at.desc(), Message.id.desc()))
                clarification_reason = (latest_turn.failure_code
                    if latest_turn and latest_turn.state == "partial" else None)
                clarification_id = (latest_turn.assistant_message_id
                    if clarification_reason and clarification_reason.startswith("needs_input") else None)
                pending_action = (clarification_reason.split(":", 1)[1]
                    if clarification_reason and clarification_reason.startswith("needs_input:")
                    else None)
                if pending_action not in REQUIRED_USER_VALUES:
                    pending_action = None
                pending_question = (db.get(Message, clarification_id).content
                    if clarification_id else None)
                history = db.scalars(select(Message).where(
                    Message.conversation_id == cid,
                    ((Message.status == "completed") |
                     (Message.id == clarification_id if clarification_id else False)))
                    .order_by(Message.created_at, Message.id)).all()
                context = build_context(history, body.content,
                                        max_messages=settings.max_history_messages,
                                        max_chars=settings.context_max_chars,
                                        clarification_message_id=clarification_id)
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
        goal_store = ConversationGoalStore(Session, cid)
        previous_goal = None
        parsed_result = None
        began = time.monotonic()
        understanding_attempts = []
        status, answer = "provider_error", ""
        result = None
        failure_code = None
        decision_steps = 0
        try:
            existing_checkpoint = plan_store.load()
            interpretation = None
            if body.goal_confirmation is not None:
                if (settings.understanding_strategy != "structured"
                        or body.goal_confirmation not in {"accept", "reject"}
                        or body.expected_goal_version is None):
                    raise UnderstandingError("invalid_goal_confirmation")
                previous_goal = goal_store.load()
                if previous_goal["version"] != body.expected_goal_version:
                    raise UnderstandingError("stale_goal_confirmation")
                if not previous_goal.get("pending") or (
                        previous_goal["pending"].get("change") or {}).get("kind") != "revise":
                    raise UnderstandingError("no_pending_revision")
                result = {"run_id": turn_id, "status": "completed",
                          "answer": "目标更正已确认。" if body.goal_confirmation == "accept"
                                    else "已拒绝目标更正，原目标保持不变。",
                          "trace": [{"step": 0, "type": "goal_confirmation"}], "steps": 0}
            if settings.understanding_strategy not in {"direct", "structured"}:
                raise UnderstandingError("invalid_understanding_strategy")
            pending_reply_kind = None
            if result is None and settings.understanding_strategy == "structured":
                previous_goal = goal_store.load()
                service = UnderstandingService(
                    HTTPUnderstandingModel(settings.model_provider, settings.model_base_url,
                                           settings.model_api_key, settings.model_timeout_seconds,
                                           settings.model_max_output_tokens),
                    settings.understanding_light_model, settings.understanding_strong_model)
                summary = [
                    {"id": item.get("id"), "goal": item.get("goal"),
                     "status": item.get("status"),
                     "revision": existing_checkpoint.get("revision")}
                    for item in (existing_checkpoint or {}).get("steps", [])[:8]]
                parsed = await service.understand(UnderstandingContext(
                    current_message=body.content, recent_completed_messages=context,
                    current_plan_summary=summary,
                    active_goal=previous_goal['goal'],
                    confirmed_constraints=previous_goal['goal'].get('constraints', []) if previous_goal['goal'] else [],
                    outstanding_clarification=(pending_question or
                        (previous_goal['pending'] or {}).get('clarification', {}).get('question'))))
                parsed_result = parsed["result"]
                interpretation = parsed_result.model_dump()
                if (pending_action in REQUIRED_USER_VALUES
                        and parsed["result"].clarification.required
                        and body.goal_confirmation is None):
                    pending_reply_kind = await classify_pending_reply(
                        HTTPUnderstandingModel(
                            settings.model_provider, settings.model_base_url,
                            settings.model_api_key, settings.model_timeout_seconds,
                            settings.model_max_output_tokens),
                        settings.understanding_strong_model,
                        question=pending_question or REQUIRED_USER_VALUES[pending_action][1],
                        reply=body.content, tool=pending_action)
                if (parsed["result"].clarification.required
                        and pending_reply_kind != "answer"):
                    result = {"run_id": turn_id, "status": "partial",
                              "answer": parsed["result"].clarification.question,
                              "trace": [{"step": 0, "type": "goal_clarification"}],
                              "steps": 0}
            # Resume only a trustworthy, latest-turn missing-argument request.
            # Semantic change classification remains S25-01's responsibility.
            # The *current user's own reply* is the only source for the new value.
            if (result is None and pending_action in REQUIRED_USER_VALUES
                    and parsed_result is not None
                    and ((parsed_result.status == "understood"
                          and parsed_result.change.kind == "clarify_response")
                         or pending_reply_kind == "answer")
                    and body.goal_confirmation is None):
                field, question = REQUIRED_USER_VALUES[pending_action]
                candidate = body.content.strip()
                try:
                    validated = SCHEMAS[pending_action].model_validate({field: candidate})
                except ValueError:
                    result = {"run_id": turn_id, "status": "partial",
                              "termination_reason": "needs_input", "pending_action": pending_action,
                              "answer": question, "trace": [{"step": 0, "type": "argument_clarification"}],
                              "steps": 0}
                else:
                    # A pending clarification is not fresh execution authorization.
                    # For a task creation, the previous request explicitly authorized
                    # the operation; the reply supplies only its missing title.
                    # Reference lookup is read-only. These are the only two allowed
                    # resumable tool types, not an arbitrary model-supplied tool name.
                    if pending_action == "create_task":
                        observed = execute_tool(pending_action, validated.model_dump(), [],
                            task_store=ConversationTaskStore(Session, cid))
                    else:
                        observed = execute_tool(pending_action, validated.model_dump(), [],
                            mcp_client=StdioMCPClient())
                    result = {"run_id": turn_id,
                              "status": "completed" if observed.get("ok") else "partial",
                              "termination_reason": ("verified_clarification_execution"
                                    if observed.get("ok") else "clarification_tool_failed"),
                              "answer": ("已创建任务：" + candidate if pending_action == "create_task"
                                  and observed.get("ok") else
                                  "已查询词条：" + candidate + "。" + str(
                                      observed.get("definition", observed.get("value", "")))
                                  if pending_action == "mcp_reference_lookup" and observed.get("ok")
                                  else "工具执行未成功，请检查查询或任务服务。"),
                              "trace": [{"step": 0, "type": "clarification_resumed",
                                         "name": pending_action, "ok": bool(observed.get("ok"))}],
                              "steps": 1}
            execution_mode = None
            execution_target = "tool_action" if (result is not None and
                any(t.get("type") == "clarification_resumed" for t in result.get("trace", []))) else "goal_state"  # Legacy injected strategy conservatively preserves revisions.
            if result is None and interpretation is not None:
                if execution_policy is not None:
                    policy_result = await execution_policy(body.content, interpretation)
                    execution_mode = (policy_result.mode if hasattr(policy_result, "mode")
                                      else policy_result)
                    if hasattr(policy_result, "target"):
                        execution_target = policy_result.target
                elif agent_decider is None:
                    policy_result = await decide_execution_policy_detail(
                        HTTPUnderstandingModel(settings.model_provider, settings.model_base_url,
                                               settings.model_api_key, settings.model_timeout_seconds,
                                               settings.model_max_output_tokens),
                        settings.understanding_strong_model, body.content, interpretation)
                    if policy_result.mode == "respond_only":
                        policy_result = await verify_text_only_policy(
                            HTTPUnderstandingModel(settings.model_provider,
                                                   settings.model_base_url,
                                                   settings.model_api_key,
                                                   settings.model_timeout_seconds,
                                                   settings.model_max_output_tokens),
                            settings.understanding_strong_model,
                            body.content, interpretation, policy_result)
                    if policy_result.mode == "execute":
                        policy_result = await verify_execute_policy(
                            HTTPUnderstandingModel(settings.model_provider,
                                                   settings.model_base_url,
                                                   settings.model_api_key,
                                                   settings.model_timeout_seconds,
                                                   settings.model_max_output_tokens),
                            settings.understanding_strong_model,
                            body.content, interpretation, policy_result)
                    execution_mode = policy_result.mode
                    execution_target = policy_result.target
                # Injected deciders in existing offline tests use legacy tool visibility;
                # live requests and explicit policy injections exercise the new boundary.
            # Only persistent execution proposals need a confirmed durable goal.
            # A revision of an unsaved conversational draft must not require
            # Goal Store state, but it must also never mutate that store.
            if result is None and parsed_result is not None:
                # Host-grounded absence of a durable referent is a clarification,
                # not a provider outage. Never invent the missing target.
                if (previous_goal is not None and previous_goal.get("goal") is None
                        and parsed_result.status == "understood"
                        and parsed_result.change.kind in {"revise", "add"}
                        and execution_mode == "execute"
                        and execution_target == "goal_state"):
                    result = {"run_id": turn_id, "status": "partial",
                              "answer": "当前没有已确认的目标。请说明要修改或追加的是哪份清单，以及具体修改内容。",
                              "trace": [{"step": 0, "type": "goal_clarification"}],
                              "steps": 0}
                if (result is None and execution_mode not in {"respond_only", "read_only"}
                        and execution_target == "goal_state"):
                    transition = goal_store.validate_before_execution(
                        parsed_result, previous_goal)
                    if transition == "confirmation_required":
                        result = {"run_id": turn_id, "status": "partial",
                                  "answer": "原有约束发生更正，请确认或拒绝。",
                                  "trace": [{"step": 0, "type": "goal_confirmation_required"}],
                                  "steps": 0, "proposed_goal": parsed_result.goal.model_dump(),
                                  "expected_goal_version": previous_goal["version"] + 1}
            if result is None:
                decider = agent_decider if agent_decider is not None else NativeToolDecision(settings)
                result = await AgentLoop(decider, task_store=ConversationTaskStore(Session, cid),
                    memory_store=MemoryStore(Session, uid, write_allowed=body.memory_write_consent),
                    mcp_client=StdioMCPClient(), skill_strategy=settings.skill_strategy,
                    skill_budget_chars=settings.skill_budget_chars).run(body.content, history=context,
                                          plan_checkpoint=existing_checkpoint,
                                          understanding_result=interpretation,
                                          execution_mode=execution_mode)
            if result["status"] in {"completed", "partial"}:
                answer, status = result["answer"], result["status"]
                if status == "partial" and (
                    result.get("termination_reason") == "needs_input"
                    or any(t.get("type") == "goal_clarification"
                           for t in result.get("trace", []))):
                    failure_code = ("needs_input:" + result["pending_action"]
                                    if result.get("pending_action") in REQUIRED_USER_VALUES
                                    else "needs_input")
            else:
                status = result["status"]
                failure_code = result.get("failure_code") or status
            decision_steps = result.get("steps", 0)
        except UnderstandingError as exc:
            status, answer = "provider_error", ""
            failure_code = exc.code
            # Only fixed-category metadata; never return prompts, values, stack traces,
            # arbitrary exception messages, model output, or credentials.
            roles = {"light", "strong"}
            outcomes = {"valid", "semantic_review_required", "structured_schema_error",
                        "structured_consistency_error", "unverified_goal_reference",
                        "model_timeout", "model_http_status", "model_transport_error",
                        "model_invalid_response", "model_empty_content",
                        "missing_credentials", "unsupported_provider",
                        "invalid_structured_result"}
            diagnostics = {"missing_goal", "clarification_status_mismatch",
                           "clarification_requires_reason_and_question",
                           "model_level_consistency", "json_or_structure"}
            for item in exc.attempts[:4]:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                outcome = item.get("outcome")
                if role not in roles or outcome not in outcomes:
                    continue
                entry = {"role": role, "outcome": outcome}
                retry = item.get("retry")
                if type(retry) is int and 0 <= retry <= 1:
                    entry["retry"] = retry
                duration = item.get("duration_ms")
                if type(duration) is int and 0 <= duration <= 600000:
                    entry["duration_ms"] = duration
                detail = item.get("diagnostic")
                if detail in diagnostics:
                    entry["diagnostic"] = detail
                elif isinstance(detail, str) and detail in {
                        "field:goal", "field:change", "field:clarification",
                        "field:status", "field:schema_version"}:
                    entry["diagnostic"] = detail
                understanding_attempts.append(entry)
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
                if (status in {"completed", "partial"} and parsed_result is not None
                        and execution_mode not in {"respond_only", "read_only"}
                        and execution_target == "goal_state"
                        and previous_goal is not None
                        and not any(item.get("type") == "goal_clarification"
                                    for item in (result or {}).get("trace", []))
                        and not any(item.get("type") == "repeat_tool_stop"
                                    for item in (result or {}).get("trace", []))):
                    goal_store.save_in_session(db, parsed_result, previous_goal)
                if status == "completed" and body.goal_confirmation is not None and previous_goal is not None:
                    goal_store.resolve_confirmation_in_session(
                        db, previous_goal, body.expected_goal_version,
                        body.goal_confirmation == "accept")
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
                                      "decision_steps": decision_steps,
                                      "trace_summary": safe_trace,
                                       **({"understanding_attempts": understanding_attempts}
                                          if failure_code == "understanding_unavailable"
                                          else {})})
        return {**result, "conversation_id": cid, "message_id": assistant_id}

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
