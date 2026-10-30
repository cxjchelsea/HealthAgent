"""Regression: an M5 plan_missing proposal does not authorize plan_not_found."""
import json

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.plan_intent import PlanRequest
from agent.decision import Decision
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


class NoToolDecision:
    async def decide(self, messages, tools):
        return Decision("已处理普通请求", [])


async def understood(self, *, model, messages, response_schema=None):
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "查询信息", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
    }, ensure_ascii=False)


@pytest.mark.parametrize("message,confirmed,expected_missing", [
    ("请查看我以前列的待办事项", False, False),
    ("请在参考目录查询 MCP 的定义", False, False),
    ("请继续执行之前保存的计划", True, True),
])
def test_missing_plan_proposal_requires_independent_review(
        tmp_path, monkeypatch, message, confirmed, expected_missing):
    calls = {"review": 0}

    async def primary(*args, **kwargs):
        return PlanRequest(intent="resume", steps=[])

    async def review(*args, **kwargs):
        calls["review"] += 1
        return confirmed

    async def policy(message, interpretation):
        return "read_only"

    monkeypatch.setattr("apps.api.main.propose_new_plan_route", primary)
    monkeypatch.setattr("apps.api.main.review_missing_plan_reference", review)
    monkeypatch.setattr("apps.api.main.NativeToolDecision",
                        lambda settings: NoToolDecision())
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", understood)

    settings = Settings(
        database_url=f"sqlite:///{tmp_path/'missing.db'}",
        model_provider="ollama_proxy", routing_profile="unified",
    )
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": message, "client_request_id": "s25-04-plan-boundary-001",
        })
    assert response.status_code == 200, response.text
    assert calls["review"] == 1
    assert (response.json().get("termination_reason") == "plan_not_found") is expected_missing
