"""M4 fault boundaries: provider error before dispatch and failure after claim.

These injected tests verify local durable cleanup; they do not assert
exactly-once external tool effects after process termination.
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.plan_intent import PlanRequest
from agent.understanding import UnderstandingError, UnderstandingResult, UnderstandingService
from agent.loop import AgentLoop
from apps.api.main import create_app
from services.db import ConversationPlan, PlanExecutionClaim, Turn, database


def understood():
    return UnderstandingResult.model_validate({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "保存两步计算计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    })


def checkpoint_steps():
    return [
        {"goal": "计算 4*7", "expected_tool": "calculate",
         "expected_arguments": {"expression": "4*7"}},
        {"goal": "计算 9+3", "expected_tool": "calculate",
         "expected_arguments": {"expression": "9+3"}},
    ]


def test_guarded_provider_timeout_before_tool_keeps_saved_plan(tmp_path, monkeypatch):
    import apps.api.main as api
    from agent.understanding import HTTPUnderstandingModel

    async def fake_complete(self, *, messages, model, response_schema=None):
        raise UnderstandingError("model_timeout")
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'timeout.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured",
                        route_admission_mode="guarded")
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages",
                               json={"content": "请创建一个两步计算计划",
                                     "client_request_id": "m4-timeout-001"})
        assert response.status_code == 503
    engine, Session = database(settings.database_url)
    with Session() as db:
        turns = list(db.scalars(select(Turn).where(Turn.conversation_id == cid)))
        assert len(turns) == 1 and turns[0].state == "provider_error"
        assert db.get(ConversationPlan, cid) is None
        assert db.get(PlanExecutionClaim, cid) is None
    engine.dispose()


def test_guarded_post_tool_exception_blocks_automatic_replay(
        tmp_path, monkeypatch):
    import apps.api.main as api
    from agent.execution_policy import ExecutionPolicyDecision

    async def fake_understand(self, context):
        return {"result": understood(), "attempts": []}

    async def intent(provider, model, message, existing):
        return (PlanRequest(intent="execute_first") if existing else
                PlanRequest(intent="plan_only", steps=checkpoint_steps()))

    async def allow(*args, **kwargs):
        return True

    async def policy(*args, **kwargs):
        return ExecutionPolicyDecision(mode="read_only", target="tool_action")

    events = []
    async def simulated_interruption(self, *args, **kwargs):
        guard = self.execution_guard
        assert guard is not None
        assert guard()
        from agent.tools import execute_tool
        observation = execute_tool("calculate", {"expression": "4*7"}, [])
        assert observation["ok"] and observation["value"] == 28
        events.append("tool_executed")
        raise RuntimeError("injected_after_tool_before_checkpoint")

    monkeypatch.setattr(UnderstandingService, "understand", fake_understand)
    monkeypatch.setattr(api, "review_plan_intent", intent)
    monkeypatch.setattr(api, "review_plan_creation_reference", allow)
    monkeypatch.setattr(api, "review_plan_execution_authorization", allow)
    monkeypatch.setattr(api, "decide_execution_policy_detail", policy)
    monkeypatch.setattr(AgentLoop, "run", simulated_interruption)

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'failure.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured",
                        route_admission_mode="guarded")
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        created = client.post(endpoint, json={
            "content": "请创建两步计划：第一步计算 4*7，第二步计算 9+3，只保存不执行。",
            "client_request_id": "m4-failure-create-001"})
        assert created.status_code == 200, created.text
        assert created.json()["termination_reason"] == "plan_created_pending_execution"
        failed = client.post(endpoint, json={
            "content": "现在只执行已保存计划的第一步。",
            "client_request_id": "m4-failure-execute-002"})
        assert failed.status_code == 503, failed.text
        assert events == ["tool_executed"]
        retry = client.post(endpoint, json={
            "content": "继续执行已保存计划剩余步骤。",
            "client_request_id": "m4-failure-retry-003"})
        assert retry.status_code == 200, retry.text
        assert retry.json()["termination_reason"] == "plan_execution_reconciliation_required"
        assert retry.json()["steps"] == 0
        assert events == ["tool_executed"]

    engine, Session = database(settings.database_url)
    with Session() as db:
        turns = list(db.scalars(select(Turn).where(Turn.conversation_id == cid)))
        assert len(turns) == 3
        assert any(t.state == "provider_error" and
                   t.failure_code == "plan_execution_outcome_unknown" for t in turns)
        assert db.get(PlanExecutionClaim, cid) is None
        saved = db.get(ConversationPlan, cid)
        assert saved is not None
        snapshot = json.loads(saved.snapshot)
        assert [step["status"] for step in snapshot["steps"]] == ["pending", "pending"]
    engine.dispose()
