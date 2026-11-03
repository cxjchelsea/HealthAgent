"""M5 regression: trustworthy pending clarifications take priority over new routes."""
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app
from services.db import AgentAttempt, ConversationPlan, Message, Turn, database
from sqlalchemy import select


def understood():
    return json.dumps({
        "schema_version": "s25-01-v1",
        "status": "understood",
        "goal": {"objective": "查询术语", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "clarify_response", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    })


@pytest.mark.parametrize("with_saved_plan", [False, True])
def test_pending_origin_is_not_sent_to_fresh_plan_primary(
        tmp_path, monkeypatch, with_saved_plan):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'pending.db'}",
        model_provider="ollama_proxy",
        understanding_strategy="structured",
        route_admission_mode="guarded",
        m5_unified_route_enabled=True,
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("pending reply must not be freshly classified as a plan")

    async def fake_complete(self, *, model, messages, response_schema=None):
        return understood()

    async def pending_answer(*args, **kwargs):
        return "answer"

    async def old_plan(*args, **kwargs):
        raise AssertionError("unified pending continuation cannot invoke legacy plan intent")

    async def policy(message, interpretation):
        return "read_only"

    monkeypatch.setattr("apps.api.main.propose_saved_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.propose_new_plan_route", forbidden)
    monkeypatch.setattr("apps.api.main.review_plan_intent", old_plan)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", old_plan)
    monkeypatch.setattr("apps.api.main.review_plan_revision", old_plan)
    monkeypatch.setattr("apps.api.main.classify_pending_reply", pending_answer)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    def no_legacy_dispatch(*args, **kwargs):
        raise AssertionError("Unified Pending must use journaled Host executor")

    monkeypatch.setattr("apps.api.main.execute_tool", no_legacy_dispatch)
    def observed_lookup(name, arguments, *args, **kwargs):
        assert name == "mcp_reference_lookup"
        assert arguments == {"term": "RAG"}
        return {"ok": True, "definition": "test definition"}

    monkeypatch.setattr(
        "agent.primary_pending_readonly_v1.execute_tool", observed_lookup)

    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        original = None
        with Session.begin() as db:
            pending = Message(conversation_id=cid, role="assistant",
                              content="请提供要查询的词条", status="partial")
            db.add(pending)
            db.flush()
            db.add(Turn(conversation_id=cid, request_id="m5-pending-prior-001",
                        state="partial", assistant_message_id=pending.id,
                        failure_code="needs_input:mcp_reference_lookup"))
            if with_saved_plan:
                original = {"revision": 1, "next_step_id": 3, "steps": [
                    {"id": 1, "goal": "计算3*4", "status": "pending",
                     "expected_tool": "calculate",
                     "expected_arguments": {"expression": "3*4"}},
                    {"id": 2, "goal": "计算5*6", "status": "pending",
                     "expected_tool": "calculate",
                     "expected_arguments": {"expression": "5*6"}},
                ]}
                db.add(ConversationPlan(
                    conversation_id=cid,
                    snapshot=json.dumps(original, ensure_ascii=False)))
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "RAG", "client_request_id": "m5-pending-followup-001"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["termination_reason"] == "verified_clarification_execution"
        assert payload["steps"] == 1
        with Session() as db:
            evidence = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            assert len(evidence) == 1
            assert evidence[0].tool_name == "mcp_reference_lookup"
            assert evidence[0].status == "succeeded"
            saved = db.get(ConversationPlan, cid)
            if with_saved_plan:
                assert json.loads(saved.snapshot) == original
            else:
                assert saved is None


def test_default_unified_old_task_pending_without_authority_never_dispatches(
        tmp_path, monkeypatch):
    """Old needs_input failure code cannot authorize a task mutation."""
    from sqlalchemy import select
    from services.db import ConversationTask, PendingWriteAuthority, AgentAttempt

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'pending_write.db'}",
        model_provider="ollama_proxy",
        understanding_strategy="structured",
        route_admission_mode="guarded",
        m5_unified_route_enabled=True,
    )

    async def fake_complete(self, *, model, messages, response_schema=None):
        return understood()

    async def pending_answer(*args, **kwargs):
        return "answer"

    async def policy(message, interpretation):
        return "read_only"

    def forbidden(*args, **kwargs):
        raise AssertionError("old Unified Pending attempted direct dispatch")

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    monkeypatch.setattr("apps.api.main.classify_pending_reply", pending_answer)
    monkeypatch.setattr("apps.api.main.execute_tool", forbidden)
    monkeypatch.setattr("agent.primary_pending_readonly_v1.execute_tool", forbidden)
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(settings.database_url)
        with Session.begin() as db:
            pending = Message(conversation_id=cid, role="assistant",
                              content="请提供任务标题", status="partial")
            db.add(pending)
            db.flush()
            db.add(Turn(conversation_id=cid, request_id="old-task-origin-001",
                        state="partial", assistant_message_id=pending.id,
                        failure_code="needs_input:create_task"))
        resp = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "提交测试报告",
            "client_request_id": "old-task-reply-001"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["termination_reason"] == "pending_write_origin_unverified"
        with Session() as db:
            assert db.scalars(select(ConversationTask)).all() == []
            assert db.scalars(select(AgentAttempt)).all() == []
            assert db.scalars(select(PendingWriteAuthority)).all() == []


def test_default_unified_trusted_pending_task_uses_single_use_authority(
        tmp_path, monkeypatch):
    """Even before default Primary cutover, an existing v1 permit can be consumed."""
    from sqlalchemy import select
    from services.db import AgentAttempt, ConversationTask, PendingWriteAuthority
    from services.primary_pending_write import mint_pending_write_authority_in_session

    cfg = Settings(
        database_url=f"sqlite:///{tmp_path / 'pending_permitted.db'}",
        model_provider="ollama_proxy", understanding_strategy="structured",
        route_admission_mode="guarded", m5_unified_route_enabled=True,
    )

    async def fake_complete(self, *, model, messages, response_schema=None):
        return understood()

    async def pending_answer(*args, **kwargs):
        return "answer"

    async def policy(message, interpretation):
        return "read_only"

    def forbidden(*args, **kwargs):
        raise AssertionError("Unified pending resumed via legacy execute_tool")

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    monkeypatch.setattr("apps.api.main.classify_pending_reply", pending_answer)
    monkeypatch.setattr("apps.api.main.execute_tool", forbidden)

    with TestClient(create_app(cfg, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        _, Session = database(cfg.database_url)
        with Session.begin() as db:
            original_user = Message(conversation_id=cid, role="user",
                                    content="请创建一个任务，先告诉我要提供标题。")
            pending = Message(conversation_id=cid, role="assistant",
                              content="请提供任务标题", status="partial")
            db.add_all([original_user, pending])
            db.flush()
            old = Turn(conversation_id=cid, request_id="permit-origin-001",
                       state="running", assistant_message_id=pending.id)
            db.add(old)
            db.flush()
            mint_pending_write_authority_in_session(
                db, conversation_id=cid, origin_turn_id=old.id,
                origin_user_message_id=original_user.id,
                source_message=original_user.content, tool_name="create_task")
            old.state = "partial"
            old.failure_code = "needs_input:create_task"

        reply = {"content": "提交测试报告",
                 "client_request_id": "permit-response-001"}
        response = client.post(f"/conversations/{cid}/agent/messages",
                               json=reply)
        assert response.status_code == 200, response.text
        assert response.json()["termination_reason"] == "pending_task_created"
        assert client.post(f"/conversations/{cid}/agent/messages",
                           json=reply).status_code == 409
        with Session() as db:
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            receipts = db.scalars(select(AgentAttempt).where(
                AgentAttempt.conversation_id == cid)).all()
            permit = db.scalar(select(PendingWriteAuthority).where(
                PendingWriteAuthority.conversation_id == cid))
            assert len(tasks) == len(receipts) == 1
            assert tasks[0].title == "提交测试报告"
            assert receipts[0].status == "succeeded"
            assert permit.status == "consumed"
