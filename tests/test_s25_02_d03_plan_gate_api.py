"""D03-F01: plan authoring cannot execute; explicit step commands resume saved evidence."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.plan_intent import PlanRequest
from apps.api.main import create_app


def understood():
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "按步骤处理只读计算", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None}
    }, ensure_ascii=False)


def test_d03_create_first_resume_no_premature_tool_calls(tmp_path, monkeypatch):
    async def fake_complete(self, *, messages, model, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    calls = []
    async def fake_plan_review(provider, model, message, existing_plan):
        calls.append((message, existing_plan))
        if len(calls) == 1:
            return PlanRequest(intent="plan_only", steps=[
                {"goal": "计算 2+2", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "2+2"}},
                {"goal": "计算 3+3", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "3+3"}},
            ])
        if len(calls) == 2:
            return PlanRequest(intent="execute_first")
        return PlanRequest(intent="resume")
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_review)
    async def policy(message, understanding):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'d03.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        route = f"/conversations/{cid}/agent/messages"
        def send(message, suffix):
            res=client.post(route,json={"content":message, "client_request_id":suffix})
            assert res.status_code == 200, res.text
            return res.json()
        first=send("建立计划：第一步算2+2，第二步算3+3", "plan-01")
        assert first["termination_reason"] == "plan_created_pending_execution"
        assert first["plan_revision"] == 1
        assert [s["status"] for s in first["plan"]] == ["pending","pending"]
        assert not any(x["type"] == "tool" for x in first["trace"])
        second=send("只执行第一步，其余保留", "plan-02")
        assert second["status"] == "partial"
        assert [s["status"] for s in second["plan"]] == ["completed","pending"]
        third=send("继续执行剩余步骤", "plan-03")
        assert third["status"] == "completed"
        assert [s["status"] for s in third["plan"]] == ["completed","completed"]
        assert len([x for x in third["trace"] if x["type"] == "tool"]) == 1
        assert calls == [(calls[0][0],False),(calls[1][0],True),(calls[2][0],True)]


def test_d03_rejects_unsupported_and_ungrounded_plan_binding():
    from agent.plan_intent import build_plan_checkpoint
    import pytest
    with pytest.raises(ValueError):
        build_plan_checkpoint("建立计划", PlanRequest(intent="plan_only",steps=[
            {"goal":"调用Shell","expected_tool":"shell",
             "expected_arguments":{"command":"echo hi"}}]))
    with pytest.raises(ValueError):
        build_plan_checkpoint("建立计划计算 1+1", PlanRequest(intent="plan_only",steps=[
            {"goal":"计算 100+100","expected_tool":"calculate",
             "expected_arguments":{"expression":"100+100"}}]))
