"""M4: opt-in guarded cancellation never grants tool or database authority."""
import json

from fastapi.testclient import TestClient
from agent.config import Settings
from agent.m4_guarded_cancel import admit_guarded_plan_cancel
from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


def test_m4_admission_negative_and_positive():
    checkpoint = {"revision": 1, "steps": [
        {"id": 1, "status": "pending", "goal": "计算 2+2",
         "expected_tool": "calculate", "expected_arguments": {"expression": "2+2"}}]}
    args = dict(message="取消刚才保存的计划，不要执行其中的步骤。",
                conversation_id="cid", request_id="request-001",
                checkpoint=checkpoint, turn_created=True,
                semantic_confirmed=True)
    assert admit_guarded_plan_cancel(**args)
    assert not admit_guarded_plan_cancel(**(args | {"semantic_confirmed": False}))
    assert not admit_guarded_plan_cancel(**(args | {"turn_created": False}))
    assert not admit_guarded_plan_cancel(**(args | {"checkpoint": None}))
    assert not admit_guarded_plan_cancel(**(args | {"message": "如何取消计划？"}))
    assert not admit_guarded_plan_cancel(**(args | {"message": "执行当前保存的计划"}))
    assert not admit_guarded_plan_cancel(**(args | {"checkpoint": {
        "revision": 0, "steps": checkpoint["steps"]}}))


def test_m4_default_legacy_and_explicit_guarded_setting():
    assert Settings().route_admission_mode == "legacy"
    assert Settings(route_admission_mode="guarded").route_admission_mode == "guarded"


def test_m4_guarded_cancel_live_api_contract_without_tools(tmp_path, monkeypatch):
    from agent.loop import AgentLoop

    understood = json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "保存计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None}
    }, ensure_ascii=False)

    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood

    async def fake_plan_intent(provider, model, message, existing_plan):
        return PlanRequest(intent="plan_only", steps=[
            {"goal": "计算 12*13", "expected_tool": "calculate",
             "expected_arguments": {"expression": "12*13"}},
            {"goal": "计算 7*9", "expected_tool": "calculate",
             "expected_arguments": {"expression": "7*9"}},
        ])

    async def confirm_create(*args, **kwargs):
        return True

    async def confirm_cancel(provider, model, message):
        return message.startswith("取消刚才")

    async def forbidden_run(self, *args, **kwargs):
        raise AssertionError("cancel must not invoke AgentLoop")

    async def policy(message, interpretation):
        return "read_only"

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_intent)
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", confirm_create)
    monkeypatch.setattr("apps.api.main.review_plan_cancellation", confirm_cancel)
    monkeypatch.setattr(AgentLoop, "run", forbidden_run)

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'm4_guarded.db'}",
        model_provider="ollama_proxy", understanding_strategy="structured",
        route_admission_mode="guarded")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        created = client.post(endpoint, json={
            "content": "请创建一个两步计划：第一步计算 12*13，第二步计算 7*9。只保存计划，不执行。",
            "client_request_id": "m4-guarded-create-001"})
        assert created.status_code == 200, created.text
        assert created.json()["termination_reason"] == "plan_created_pending_execution"
        cancelled = client.post(endpoint, json={
            "content": "取消刚才保存的计划，不要执行其中的步骤。",
            "client_request_id": "m4-guarded-cancel-002"})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["termination_reason"] == "plan_cancelled"
        assert cancelled.json()["trace"] == [{"step": 0, "type": "plan_cancelled"}]
        assert cancelled.json()["plan"] == []
