from agent.planning import PlanLedger
import json
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
from agent.execution_policy import decide_execution_policy_detail, reconcile_execution_policy
from agent.route_shadow import evaluate_shadow, compare_to_legacy
from agent.route_shadow_host import facts_from_legacy_observation
from agent.m4_guarded_cancel import admit_guarded_plan_cancel
from agent.m4_guarded_plan import admit_guarded_plan_create
from agent.m4_guarded_execution import admit_guarded_plan_execution
from agent.m3_turn_capture import (
    capture_turn, compare_captured_identity, compare_checkpoint_snapshot,
)
from agent.decision import NativeToolDecision
from agent.loop import AgentLoop
from agent.tools import execute_tool, SCHEMAS
from agent.argument_provenance import REQUIRED_USER_VALUES
from agent.pending_reply import classify_pending_reply
from agent.pending_reference_review import review_pending_reference_lookup
from agent.capability_review import review_unsupported_capability
from agent.clarification_review import review_read_only_clarification
from agent.control_admission import admit_clarification
from agent.plan_intent import review_plan_intent, build_plan_checkpoint
from agent.plan_control import (resolve_plan_admission, review_missing_plan_reference,
                                review_plan_creation_reference, explicit_deferred_plan_request,
                                review_plan_cancellation, explicit_saved_plan_cancellation)
from agent.plan_execution_review import review_plan_execution_authorization
from agent.plan_execution_scope import review_plan_execution_scope
from agent.plan_revision_review import review_plan_revision, apply_plan_revision, check_plan_edit_intent, explicit_step_binding_conflict
from agent.tools import TOOL_SCHEMAS
from agent.mcp_stdio_client import StdioMCPClient
from agent.context import build_context
from services.task_store import ConversationTaskStore
from services.memory_store import MemoryStore
from services.plan_store import ConversationPlanStore, PlanSnapshotConflict
from services.goal_store import ConversationGoalStore
from services.db import Conversation, ConversationPlan, Message, Turn, database

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
                # M3-B: capture Turn identity and plan metadata inside the same
                # DB transaction that creates the Turn. This is read-only and
                # grants no Host execution authority.
                plan_record = db.get(ConversationPlan, cid)
                preturn_plan_raw = plan_record.snapshot if plan_record else None
                try:
                    preturn_checkpoint = (json.loads(preturn_plan_raw)
                                          if preturn_plan_raw is not None else None)
                except (TypeError, ValueError):
                    preturn_checkpoint = None
                preturn_capture = None
                if settings.route_shadow_enabled:
                    preturn_capture = capture_turn(
                        conversation_id=cid, request_id=body.client_request_id,
                        turn_id=turn_id, checkpoint=preturn_checkpoint,
                        pending_action=pending_action, clarification_id=clarification_id,
                        pending_question=pending_question)
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
        execution_claim_acquired = False
        goal_store = ConversationGoalStore(Session, cid)

        def saved_plan_execution_guard():
            nonlocal execution_claim_acquired
            # Claim the exact Turn-bound snapshot in a DB transaction before
            # dispatch. Every supported plan writer checks this same claim.
            if preturn_plan_raw is None:
                return False
            try:
                with Session.begin() as guard_db:
                    plan_store.acquire_execution_claim_in_session(
                        guard_db, turn_id=turn_id, expected_raw=preturn_plan_raw)
                execution_claim_acquired = True
                return True
            except (PlanSnapshotConflict, IntegrityError):
                return False

        previous_goal = None
        parsed_result = None
        began = time.monotonic()
        understanding_attempts = []
        status, answer = "provider_error", ""
        result = None
        failure_code = None
        decision_steps = 0
        try:
            # Use the exact snapshot observed in the Turn-creation transaction.
            # Do not perform a second, unbound plan read after creating the Turn.
            existing_checkpoint = (json.loads(preturn_plan_raw)
                                   if preturn_plan_raw is not None else None)
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
            # A confirmed cancellation is a Host-owned plan mutation, not an
            # AgentLoop tool. The exact pre-turn snapshot is CAS-deleted during
            # terminal Turn commit. No tools or step execution occur here.
            if (result is None and existing_checkpoint is not None
                    and settings.understanding_strategy == "structured"
                    and agent_decider is None and body.goal_confirmation is None
                    and await review_plan_cancellation(
                        HTTPUnderstandingModel(
                            settings.model_provider, settings.model_base_url,
                            settings.model_api_key, settings.model_timeout_seconds,
                            settings.model_max_output_tokens),
                        settings.understanding_strong_model, body.content)):
                if settings.route_admission_mode == "guarded" and not admit_guarded_plan_cancel(
                        message=body.content, conversation_id=cid,
                        request_id=body.client_request_id,
                        checkpoint=existing_checkpoint, turn_created=True,
                        semantic_confirmed=True):
                    result = {"run_id": turn_id, "status": "partial",
                              "termination_reason": "plan_cancellation_requires_clarification",
                              "answer": "无法验证当前计划取消权限；未取消，也未执行工具。",
                              "trace": [{"step": 0, "type": "plan_cancellation_fenced"}],
                              "steps": 0, "plan": existing_checkpoint["steps"],
                              "plan_revision": existing_checkpoint["revision"]}
                else:
                    result = {"run_id": turn_id, "status": "completed",
                          "termination_reason": "plan_cancelled",
                          "answer": "已取消当前保存的计划，未执行任何步骤。",
                          "trace": [{"step": 0, "type": "plan_cancelled"}],
                          "steps": 0, "plan": [], "plan_events": [
                              {"event": "plan_cancelled",
                               "revision": existing_checkpoint["revision"]}],
                          "plan_revision": existing_checkpoint["revision"],
                          "plan_cancellation_requested": True}
            # A strict, user-grounded contradiction can be rejected before
            # invoking any classifier or generic Understanding model.
            if (result is None and existing_checkpoint
                    and settings.understanding_strategy == "structured"
                    and agent_decider is None and body.goal_confirmation is None
                    and explicit_step_binding_conflict(body.content, existing_checkpoint)):
                result = {"run_id": turn_id, "status": "partial",
                          "termination_reason": "plan_revision_conflict",
                          "answer": "指定步骤与当前计划绑定不一致，请确认要修改的步骤编号。",
                          "trace": [{"step": 0, "type": "plan_revision_conflict"}],
                          "steps": 0, "plan_checkpoint": existing_checkpoint,
                          "plan": existing_checkpoint["steps"],
                          "plan_revision": existing_checkpoint["revision"],
                          "plan_events": []}
            # Resolve plan execution intent independently and FIRST, so a
            # revision classifier cannot capture "execute first step" as an edit.
            pending_plan_execution_intent = None
            reviewed_plan_intent = None
            if (result is None and existing_checkpoint
                    and settings.understanding_strategy == "structured"
                    and agent_decider is None and body.goal_confirmation is None):
                reviewed_plan_intent = await review_plan_intent(
                    HTTPUnderstandingModel(
                        settings.model_provider, settings.model_base_url,
                        settings.model_api_key, settings.model_timeout_seconds,
                        settings.model_max_output_tokens),
                    settings.understanding_strong_model,
                    body.content, True)
                if reviewed_plan_intent.intent in {"execute_first", "resume"}:
                    pending_plan_execution_intent = reviewed_plan_intent
            # Saved plan edits have their own bounded contract. Apply only a
            # proven single pending-step binding change before generic goal
            # Understanding, which must not rewrite the PlanLedger on its own.
            if (result is None and existing_checkpoint
                    and not pending_plan_execution_intent
                    and settings.understanding_strategy == "structured"
                    and agent_decider is None and body.goal_confirmation is None):
                revision_proposal = await review_plan_revision(
                    HTTPUnderstandingModel(
                        settings.model_provider, settings.model_base_url,
                        settings.model_api_key, settings.model_timeout_seconds,
                        settings.model_max_output_tokens),
                    settings.understanding_strong_model,
                    body.content, existing_checkpoint)
                if revision_proposal.intent != "revise":
                    edit_unresolved = revision_proposal.edit_requested
                    if edit_unresolved is None:
                        # Compatibility for legacy/test proposals. Current
                        # production schema carries this semantic signal in
                        # the first revision review, avoiding another call.
                        edit_unresolved = await check_plan_edit_intent(
                            HTTPUnderstandingModel(
                                settings.model_provider, settings.model_base_url,
                                settings.model_api_key, settings.model_timeout_seconds,
                                settings.model_max_output_tokens),
                            settings.understanding_strong_model,
                            body.content, existing_checkpoint)
                    if edit_unresolved:
                        result = {"run_id": turn_id, "status": "partial",
                                  "termination_reason": "plan_revision_conflict",
                                  "answer": "无法确认计划修改目标，请明确步骤编号、原参数及新参数。",
                                  "trace": [{"step": 0, "type": "plan_revision_conflict"}],
                                  "steps": 0, "plan_checkpoint": existing_checkpoint,
                                  "plan": existing_checkpoint["steps"],
                                  "plan_revision": existing_checkpoint["revision"],
                                  "plan_events": []}
                if revision_proposal.intent == "revise":
                    revised = apply_plan_revision(
                        existing_checkpoint, body.content, revision_proposal)
                    if revised is None:
                        result = {"run_id": turn_id, "status": "partial",
                                  "termination_reason": "plan_revision_conflict",
                                  "answer": "修改描述与已保存计划的步骤或参数绑定不一致，请依据当前计划检查步骤编号与原参数。",
                                  "trace": [{"step": 0, "type": "plan_revision_conflict"}],
                                  "steps": 0,
                                  "plan_checkpoint": existing_checkpoint,
                                  "plan": existing_checkpoint["steps"],
                                  "plan_revision": existing_checkpoint["revision"],
                                  "plan_events": []}
                    else:
                        checkpoint = revised["checkpoint"]
                        result = {"run_id": turn_id, "status": "partial",
                                  "termination_reason": "plan_revised_pending_execution",
                                  "answer": "已更新计划指定步骤，其他步骤不变；本次没有执行工具。",
                                  "trace": [{"step": 0, "type": "plan_revised"}],
                                  "steps": 0, "plan_checkpoint": checkpoint,
                                  "plan": checkpoint["steps"],
                                  "plan_revision": checkpoint["revision"],
                                  "plan_events": [revised["event"]]}
            pending_reply_kind = None
            clarification_read_only = False
            suppress_persistent_routing = False
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
                        and (parsed["result"].clarification.required
                             or pending_action == "mcp_reference_lookup")
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
                        and pending_reply_kind != "answer"
                        and (settings.route_admission_mode != "guarded"
                             or pending_plan_execution_intent is None)):
                    # A pre-reviewed saved-plan execution request is not an
                    # incomplete generic goal. Keep its independent plan
                    # authorization/scope checks and final Host guards intact.
                    # A missing MCP term is a pending *argument* to an
                    # already-requested read-only operation, not a new tool
                    # capability request. Persist this source-bound state
                    # before running any unsupported-capability reviewer.
                    pending_lookup = False
                    if pending_action is None and body.goal_confirmation is None:
                        pending_lookup = await review_pending_reference_lookup(
                            HTTPUnderstandingModel(
                                settings.model_provider, settings.model_base_url,
                                settings.model_api_key, settings.model_timeout_seconds,
                                settings.model_max_output_tokens),
                            settings.understanding_strong_model, body.content)
                    if pending_lookup:
                        pending_action = "mcp_reference_lookup"
                        result = {"run_id": turn_id, "status": "partial",
                                  "termination_reason": "needs_input",
                                  "pending_action": pending_action,
                                  "answer": parsed["result"].clarification.question,
                                  "trace": [{"step": 0, "type": "goal_clarification"}],
                                  "steps": 0}
                    # Capability unavailability is not missing user information.
                    # A narrow review may *deny* a clearly named missing tool,
                    # but never grants access to that or any other tool.
                    requested_unavailable = None
                    if result is None:
                        requested_unavailable = await review_unsupported_capability(
                            HTTPUnderstandingModel(
                                settings.model_provider, settings.model_base_url,
                                settings.model_api_key, settings.model_timeout_seconds,
                                settings.model_max_output_tokens),
                            settings.understanding_strong_model, body.content,
                            {t["function"]["name"] for t in TOOL_SCHEMAS})
                    # Resolve competing model proposals through one Host route.
                    # A pending tool-argument clarification is never silently
                    # converted into a new read-only action.
                    ready_to_attempt = False
                    if (not requested_unavailable and pending_action is None
                            and body.goal_confirmation is None):
                        ready_to_attempt = await review_read_only_clarification(
                            HTTPUnderstandingModel(
                                settings.model_provider, settings.model_base_url,
                                settings.model_api_key, settings.model_timeout_seconds,
                                settings.model_max_output_tokens),
                            settings.understanding_strong_model, body.content,
                            parsed["result"].clarification.question,
                            ["calculate", "mcp_reference_lookup", "utc_time",
                             "list_tasks", "recall_memory"])
                    clarification_route = admit_clarification(
                        missing_argument_action=pending_action,
                        unsupported_name=requested_unavailable,
                        complete_read_only_request=ready_to_attempt)
                    if result is not None:
                        pass  # A trusted pending argument was already admitted.
                    elif clarification_route.kind == "unsupported":
                        result = {"run_id": turn_id, "status": "partial",
                                  "termination_reason": "unsupported_capability",
                                  "answer": "当前系统没有提供或授权 " +
                                            clarification_route.unsupported_name +
                                            " 执行工具，因此无法执行该请求。",
                                  "trace": [{"step": 0, "type": "unsupported_capability"}],
                                  "steps": 0}
                    elif clarification_route.kind == "read_only":
                        suppress_persistent_routing = clarification_route.skip_persistence
                        clarification_read_only = True
                    else:
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
            execution_mode = "read_only" if clarification_read_only else None
            execution_target = "tool_action" if (clarification_read_only or (result is not None and
                any(t.get("type") == "clarification_resumed" for t in result.get("trace", [])))) else "goal_state"  # Legacy injected strategy conservatively preserves revisions.
            if result is None and interpretation is not None and not clarification_read_only:
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
                    if policy_result.mode != "read_only":
                        policy_result = await reconcile_execution_policy(
                            HTTPUnderstandingModel(
                                settings.model_provider, settings.model_base_url,
                                settings.model_api_key, settings.model_timeout_seconds,
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
            if result is None and parsed_result is not None and not suppress_persistent_routing:
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
            plan_control = None
            confirmed_plan_execution = False
            # Separate authoring a future plan from executing its steps. Only
            # the bound checkpoint can authorize later read-only execution.
            if (result is None and parsed_result is not None
                    and not suppress_persistent_routing and agent_decider is None):
                plan_decision = reviewed_plan_intent or await review_plan_intent(
                    HTTPUnderstandingModel(settings.model_provider,
                                           settings.model_base_url,
                                           settings.model_api_key,
                                           settings.model_timeout_seconds,
                                           settings.model_max_output_tokens),
                    settings.understanding_strong_model,
                    body.content, bool(existing_checkpoint))
                # Produce a single mutually exclusive Host route from model
                # proposals and independently checked saved-plan references.
                creation_confirmed = False
                missing_reference = None
                if plan_decision.intent == "plan_only":
                    creation_confirmed = await review_plan_creation_reference(
                        HTTPUnderstandingModel(
                            settings.model_provider, settings.model_base_url,
                            settings.model_api_key, settings.model_timeout_seconds,
                            settings.model_max_output_tokens),
                        settings.understanding_strong_model, body.content)
                elif (plan_decision.intent in {"execute_first", "resume"}
                      and existing_checkpoint is None):
                    missing_reference = await review_missing_plan_reference(
                        HTTPUnderstandingModel(
                            settings.model_provider, settings.model_base_url,
                            settings.model_api_key, settings.model_timeout_seconds,
                            settings.model_max_output_tokens),
                        settings.understanding_strong_model, body.content)
                plan_route = resolve_plan_admission(
                    plan_decision, existing_checkpoint=existing_checkpoint,
                    creation_confirmed=creation_confirmed,
                    missing_reference=missing_reference)
                if plan_route.kind == "existing_plan":
                    result = {"run_id": turn_id, "status": "partial",
                              "termination_reason": "existing_plan_requires_revision",
                              "answer": "会话已有计划，请先确认修改现有计划。",
                              "trace": [{"step": 0, "type": "plan_creation_blocked"}],
                              "steps": 0}
                elif plan_route.kind == "create":
                    try:
                        checkpoint = build_plan_checkpoint(body.content, plan_decision)
                    except ValueError:
                        # Once the independent semantic review has confirmed
                        # plan authoring, a failed binding must not silently
                        # grant the ordinary AgentLoop tool execution route.
                        if settings.route_admission_mode == "guarded":
                            result = {"run_id": turn_id, "status": "partial",
                                      "termination_reason": "plan_creation_requires_clarification",
                                      "answer": "计划步骤未通过绑定校验；未保存，也未执行工具。",
                                      "trace": [{"step": 0, "type": "plan_execution_fenced"}],
                                      "steps": 0}
                        else:
                            plan_decision = None
                    else:
                        if (settings.route_admission_mode == "guarded"
                                and not admit_guarded_plan_create(
                                    message=body.content, conversation_id=cid,
                                    request_id=body.client_request_id,
                                    checkpoint=checkpoint, turn_created=True,
                                    semantic_confirmed=creation_confirmed)):
                            result = {"run_id": turn_id, "status": "partial",
                                      "termination_reason": "plan_creation_requires_clarification",
                                      "answer": "计划步骤无法逐项绑定到当前请求；未保存，也未执行工具。",
                                      "trace": [{"step": 0, "type": "plan_execution_fenced"}],
                                      "steps": 0}
                        else:
                            result = {"run_id": turn_id, "status": "partial",
                                  "termination_reason": "plan_created_pending_execution",
                                  "answer": "已建立计划，尚未执行任何步骤。请指定执行步骤。",
                                  "trace": [{"step": 0, "type": "plan_created"}],
                                  "steps": 0, "plan": checkpoint["steps"],
                                  "plan_revision": checkpoint["revision"],
                                  "plan_events": [{"event": "plan_created",
                                                   "revision": checkpoint["revision"]}],
                                  "plan_checkpoint": checkpoint}
                elif plan_route.kind == "missing_plan":
                    result = {"run_id": turn_id, "status": "partial",
                              "termination_reason": "plan_not_found",
                              "answer": "没有可继续执行的已保存计划，请先建立计划。",
                              "trace": [{"step": 0, "type": "plan_not_found"}],
                              "steps": 0}
                elif plan_route.kind in {"execute_first", "resume"}:
                    # For live structured routing, a saved-plan proposal must
                    # be independently confirmed against the CURRENT request.
                    # Rejection returns to ordinary request handling; it must
                    # never turn a standalone calculation into a plan step.
                    if agent_decider is None and execution_policy is None:
                        confirmed_plan_execution = await review_plan_execution_authorization(
                            HTTPUnderstandingModel(
                                settings.model_provider, settings.model_base_url,
                                settings.model_api_key, settings.model_timeout_seconds,
                                settings.model_max_output_tokens),
                            settings.understanding_strong_model, body.content,
                            existing_checkpoint, plan_route.kind)
                        if not confirmed_plan_execution:
                            plan_route = resolve_plan_admission(
                                type(plan_decision)(intent="other"),
                                existing_checkpoint=existing_checkpoint)
                    if plan_route.kind in {"execute_first", "resume"}:
                        # An explicit first-step request is already bounded. Only
                        # a broad resume proposal needs a separate scope check;
                        # ambiguous scope never widens single-step authority.
                        if plan_route.kind == "execute_first":
                            plan_control = "execute_first"
                        elif agent_decider is None and execution_policy is None:
                            scope = await review_plan_execution_scope(
                                HTTPUnderstandingModel(
                                    settings.model_provider, settings.model_base_url,
                                    settings.model_api_key, settings.model_timeout_seconds,
                                    settings.model_max_output_tokens),
                                settings.understanding_strong_model, body.content)
                            plan_control = "resume" if scope == "all" else "execute_first"
                        else:
                            plan_control = plan_route.kind
                        # A completed checkpoint is terminal: subsequent continuation
                        # requests are read-only status checks, never new tool work.
                        completed_plan = None
                        if existing_checkpoint:
                            try:
                                completed_plan = PlanLedger.restore(existing_checkpoint)
                            except (ValueError, TypeError):
                                completed_plan = None
                        if (completed_plan is not None
                                and completed_plan.snapshot()
                                and all(step["status"] == "completed"
                                        for step in completed_plan.snapshot())):
                            result = {"run_id": turn_id, "status": "completed",
                                      "termination_reason": "plan_already_completed",
                                      "answer": "当前计划所有步骤均已完成，没有剩余待执行步骤；本次未重新调用任何工具。",
                                      "trace": [{"step": 0, "type": "plan_already_completed"}],
                                      "steps": 0, "plan": completed_plan.snapshot(),
                                      "plan_revision": completed_plan.revision,
                                      "plan_events": [],
                                      "plan_checkpoint": existing_checkpoint}
                        elif (plan_control == "execute_first"
                              and completed_plan is not None
                              and completed_plan.snapshot()
                              and completed_plan.snapshot()[0]["status"] == "completed"):
                            result = {"run_id": turn_id, "status": "partial",
                                      "termination_reason": "plan_first_step_already_completed",
                                      "answer": "已保存计划的第一步已经完成，本轮不会执行下一步。如需执行下一未完成步骤，请明确提出。",
                                      "trace": [{"step": 0, "type": "plan_first_step_already_completed"}],
                                      "steps": 0, "plan": completed_plan.snapshot(),
                                      "plan_revision": completed_plan.revision,
                                      "plan_events": [],
                                      "plan_checkpoint": existing_checkpoint}
                        elif execution_mode == "respond_only" and existing_checkpoint:
                            # Reuse the identical current-turn authorization
                            # evidence instead of asking a model twice.
                            authorized = (confirmed_plan_execution
                                if agent_decider is None and execution_policy is None
                                else await review_plan_execution_authorization(
                                    HTTPUnderstandingModel(
                                        settings.model_provider, settings.model_base_url,
                                        settings.model_api_key, settings.model_timeout_seconds,
                                        settings.model_max_output_tokens),
                                    settings.understanding_strong_model,
                                    body.content, existing_checkpoint, plan_decision.intent))
                            if authorized:
                                # Re-review can only permit the saved, read-only,
                                # schema-bound pending steps, never arbitrary tools.
                                execution_mode = "read_only"
                                execution_target = "tool_action"
                        if result is None and execution_mode == "respond_only":
                            result = {"run_id": turn_id, "status": "partial",
                                      "termination_reason": "execution_not_authorized",
                                      "answer": "当前请求未获得计划工具执行许可。",
                                      "trace": [{"step": 0, "type": "plan_execution_denied"}],
                                      "steps": 0}
                        elif result is None and existing_checkpoint:
                            plan_control = plan_control or plan_decision.intent
            # M4 guarded mode narrows existing saved-plan execution admission
            # after the independent authorization and scope reviews. This
            # candidate NEVER bypasses the executor's durable execution claim.
            if (result is None and settings.route_admission_mode == "guarded"
                    and agent_decider is None
                    and plan_control in {"execute_first", "resume"}):
                if not admit_guarded_plan_execution(
                        conversation_id=cid, request_id=body.client_request_id,
                        checkpoint=existing_checkpoint, scope=plan_control,
                        turn_created=True,
                        semantic_confirmed=confirmed_plan_execution):
                    result = {"run_id": turn_id, "status": "partial",
                              "termination_reason": "execution_not_authorized",
                              "answer": "计划执行未通过受控准入；未调用任何工具。",
                              "trace": [{"step": 0, "type": "plan_execution_denied"}],
                              "steps": 0}
            # Never hand explicit saved-plan cancellation to the generic loop
            # when the independent semantic verifier did not confirm deletion.
            if (result is None and existing_checkpoint is not None
                    and settings.understanding_strategy == "structured"
                    and agent_decider is None
                    and explicit_saved_plan_cancellation(body.content)):
                result = {"run_id": turn_id, "status": "partial",
                          "termination_reason": "plan_cancellation_requires_clarification",
                          "answer": "无法确认取消当前计划的授权；未取消，也未执行步骤。",
                          "trace": [{"step": 0, "type": "plan_cancellation_fenced"}],
                          "steps": 0, "plan": existing_checkpoint["steps"],
                          "plan_revision": existing_checkpoint["revision"]}
            # A model's failure to recognize an explicitly saved, non-running
            # plan is not permission to invoke ordinary tools. The narrow
            # current-turn constraint fence only blocks execution; it never
            # fabricates a plan or writes a checkpoint.
            if (result is None and agent_decider is None
                    and settings.understanding_strategy == "structured"
                    and pending_plan_execution_intent is None
                    and explicit_deferred_plan_request(body.content)):
                result = {"run_id": turn_id, "status": "partial",
                          "termination_reason": "plan_creation_requires_clarification",
                          "answer": "已识别到只保存、不执行的计划约束，但当前无法可靠保存步骤；请确认计划内容。",
                          "trace": [{"step": 0, "type": "plan_execution_fenced"}],
                          "steps": 0}
            if (result is None and settings.route_admission_mode == "guarded"
                    and plan_control in {"execute_first", "resume"}):
                with Session() as recovery_db:
                    unknown = recovery_db.scalar(select(Turn.id).where(
                        Turn.conversation_id == cid,
                        Turn.failure_code == "plan_execution_outcome_unknown"))
                if unknown is not None:
                    result = {"run_id": turn_id, "status": "partial",
                              "termination_reason": "plan_execution_reconciliation_required",
                              "answer": "此前计划执行结果尚未核实。已停止后续工具调用，请先人工核对执行记录与当前计划，再决定是否恢复。",
                              "trace": [{"step": 0, "type": "plan_execution_fenced"}],
                              "steps": 0, "plan": existing_checkpoint["steps"],
                              "plan_revision": existing_checkpoint["revision"]}
            if result is None:
                decider = agent_decider if agent_decider is not None else NativeToolDecision(settings)
                result = await AgentLoop(decider, task_store=ConversationTaskStore(Session, cid),
                    memory_store=MemoryStore(Session, uid, write_allowed=body.memory_write_consent),
                    mcp_client=StdioMCPClient(), skill_strategy=settings.skill_strategy,
                    skill_budget_chars=settings.skill_budget_chars,
                    execution_guard=(saved_plan_execution_guard
                        if plan_control in {"execute_first", "resume"} else None)).run(
                                          body.content, history=context,
                                          plan_checkpoint=existing_checkpoint,
                                          understanding_result=interpretation,
                                          execution_mode=execution_mode,
                                          plan_control=plan_control)
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
            failure_code = ("plan_execution_outcome_unknown"
                            if settings.route_admission_mode == "guarded"
                            and execution_claim_acquired else "agent_exception")
        # Atomic local DB commit: never expose a successful message separately
        # from its corresponding plan checkpoint and turn terminal state.
        # External tool effects remain outside this transaction.
        try:
            with Session.begin() as db:
                response = db.get(Message, assistant_id)
                turn = db.get(Turn, turn_id)
                if response is None or turn is None or turn.state != "running":
                    raise RuntimeError("turn_finalization_conflict")
                if (status == "completed" and result
                        and result.get("plan_cancellation_requested")):
                    try:
                        plan_store.cancel_in_session(
                            db, expected_raw=preturn_plan_raw)
                    except PlanSnapshotConflict:
                        status = "partial"
                        failure_code = "plan_snapshot_conflict"
                        answer = "计划已发生变化，未取消新的计划。"
                        result = {"run_id": turn_id, "status": status,
                                  "termination_reason": "plan_snapshot_conflict",
                                  "answer": answer,
                                  "trace": [{"step": 0, "type": "plan_snapshot_conflict"}],
                                  "steps": 0}
                if status in {"completed", "partial"} and result.get("plan_checkpoint"):
                    try:
                        # Every plan checkpoint, including tool-execution evidence,
                        # must match the exact snapshot bound to this Turn.
                        plan_store.compare_and_swap_in_session(
                            db, expected_raw=preturn_plan_raw,
                            snapshot=result["plan_checkpoint"],
                            owner_turn_id=turn_id if plan_control in {"execute_first", "resume"} else None)
                    except PlanSnapshotConflict:
                        # Never overwrite a newer durable plan. If a tool was
                        # already dispatched, tell the user to verify its effect
                        # before retrying; the database cannot roll that effect back.
                        status = "partial"
                        failure_code = ("plan_execution_outcome_unknown"
                                        if settings.route_admission_mode == "guarded"
                                        and execution_claim_acquired
                                        else "plan_snapshot_conflict")
                        answer = ("当前计划已发生变化，本次没有覆盖新计划。若本轮已调用工具，"
                                  "请先核对工具执行结果，再决定是否重试，避免重复执行。")
                        result = {
                            "run_id": turn_id,
                            "status": status,
                            "termination_reason": "plan_snapshot_conflict",
                            "answer": answer,
                            "trace": [{"step": 0, "type": "plan_snapshot_conflict"}],
                            "steps": 0,
                        }
                if (status in {"completed", "partial"} and parsed_result is not None
                        and not suppress_persistent_routing and execution_mode not in {"respond_only", "read_only"}
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
                # Release any saved-plan claim in the same transaction that
                # persists the terminal Turn state, including partial/error paths.
                plan_store.release_execution_claim_in_session(db, turn_id=turn_id)
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
        # M2 diagnostic shadow is AFTER legacy durable finalization.
        # No effect on tool execution, plan mutation or persisted Turn state.
        shadow = None
        if (settings.route_shadow_enabled
                and settings.understanding_strategy == "structured"
                and settings.model_provider != "fake"
                and agent_decider is None
                and body.goal_confirmation is None):
            pending_field = (REQUIRED_USER_VALUES[pending_action][0]
                             if pending_action in REQUIRED_USER_VALUES else None)
            host_facts = facts_from_legacy_observation(
                checkpoint=existing_checkpoint,
                pending_action=pending_action,
                pending_field=pending_field,
                pending_original_request_bound=bool(
                    pending_action and clarification_id and pending_question),
                execution_mode=execution_mode,
                result=result,
                registered_tools=set(SCHEMAS),
            )
            # Diagnostics run after the durable Turn commit. Provider/schema/
            # preview failures must never turn an already committed success
            # into an HTTP error or re-dispatch a tool.
            shadow_started = time.monotonic()
            try:
                shadow = await evaluate_shadow(
                    HTTPUnderstandingModel(
                        settings.model_provider, settings.model_base_url,
                        settings.model_api_key, settings.model_timeout_seconds,
                        settings.model_max_output_tokens),
                    settings.understanding_strong_model, body.content,
                    has_plan=existing_checkpoint is not None,
                    pending_action=pending_action, pending_field=pending_field,
                    host_facts=host_facts,
                    m3c_capture=preturn_capture,
                    m3c_checkpoint=preturn_checkpoint)
            except Exception as exc:
                log.warning("shadow diagnostic unavailable turn=%s class=%s",
                            turn_id, type(exc).__name__)
                shadow = {"state": "unavailable", "error_class": "ShadowEvaluationError",
                          "host_admission_complete": False}
            shadow["diagnostic_elapsed_ms"] = int(
                (time.monotonic() - shadow_started) * 1000)
            shadow["diagnostic_model_calls"] = 1 + shadow.get("recovery_model_calls", 0)
            shadow["execution_authorized"] = False
        response_data = {**result, "conversation_id": cid,
                         "message_id": assistant_id, "turn_id": turn_id}
        if shadow is not None:
            response_data["route_shadow"] = {**shadow, **compare_to_legacy(shadow, result)}
            if preturn_capture is not None:
                response_data["route_shadow"]["m3_preturn_identity"] = (
                    compare_captured_identity(
                        preturn_capture, conversation_id=cid,
                        request_id=body.client_request_id, turn_id=turn_id))
                response_data["route_shadow"]["m3_checkpoint_snapshot"] = (
                    compare_checkpoint_snapshot(preturn_capture, existing_checkpoint))
        return response_data

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
                "configured":settings.provider_ready,
                "route_admission_mode": settings.route_admission_mode,
                "m4_contract": "guarded-plan-route-priority-v2"}

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
