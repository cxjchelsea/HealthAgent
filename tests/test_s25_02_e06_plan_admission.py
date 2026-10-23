
# These API regression fixtures explicitly supply a formal saved-plan request.
# Stub only the new semantic admission review; the actual production model
# contract is tested separately in test_s25_02_f03_plan_admission.py.
import pytest

@pytest.fixture(autouse=True)
def admit_fixture_saved_plan(monkeypatch):
    async def admitted(*args, **kwargs):
        return True
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", admitted)

"""E06 ordinary drafts and tasks are not formal executable plans."""
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from agent.plan_intent import PlanRequest
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


def understood():
    return json.dumps({"schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective":"处理当前任务","subgoals":[],"constraints":[],
                 "success_criteria":[]},
        "change":{"kind":"new_goal","target_goal_id":None,"delta":None},
        "clarification":{"required":False,"reason_code":"none","question":None}})


class FakeDecision:
    calls = 0
    def __init__(self, settings):
        pass
    async def decide(self, messages, tools):
        type(self).calls += 1
        return Decision(content="已按本轮指令处理。",tool_calls=[])


def test_nonexecutable_plan_label_does_not_intercept_normal_chat(tmp_path, monkeypatch):
    async def complete(self, *, model, messages, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel,"complete",complete)
    async def mislabeled_plan(provider, model, message, existing_plan):
        # Reproduce Qwen routing both requests into plan_only incorrectly.
        return PlanRequest(intent="plan_only", steps=[])
    monkeypatch.setattr("apps.api.main.review_plan_intent",mislabeled_plan)
    monkeypatch.setattr("apps.api.main.NativeToolDecision",FakeDecision)
    async def policy(message, result):
        return "respond_only"
    FakeDecision.calls=0
    settings=Settings(database_url=f"sqlite:///{tmp_path/'e06.db'}",
        model_provider="ollama_proxy",understanding_strategy="structured")
    with TestClient(create_app(settings,execution_policy=policy)) as client:
        cid=client.post("/conversations").json()["id"]
        route=f"/conversations/{cid}/agent/messages"
        for i,content in enumerate((
            "帮我拟定三项明天的工作任务，只给出草案，不要创建任务，也不要保存。",
            "现在请正式创建一个任务，名称是 E06权限测试任务。"
        )):
            response=client.post(route,json={"content":content,
                "client_request_id":f"e06-routing-{i+1:04d}"})
            assert response.status_code == 200,response.text
            data=response.json()
            assert data.get("termination_reason") != "plan_requires_clarification"
            assert data.get("termination_reason") != "plan_created_pending_execution"
            assert data["steps"] >= 1
            assert data.get("plan_checkpoint") is None
    assert FakeDecision.calls == 2


def test_valid_formal_plan_still_creates_checkpoint(tmp_path,monkeypatch):
    async def complete(self, *, model, messages, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel,"complete",complete)
    async def plan(provider, model, message, existing_plan):
        return PlanRequest(intent="plan_only",steps=[
            {"goal":"查询 RAG","expected_tool":"mcp_reference_lookup",
             "expected_arguments":{"term":"RAG"}},
            {"goal":"计算 8*8","expected_tool":"calculate",
             "expected_arguments":{"expression":"8*8"}}])
    monkeypatch.setattr("apps.api.main.review_plan_intent",plan)
    settings=Settings(database_url=f"sqlite:///{tmp_path/'plan.db'}",
        model_provider="ollama_proxy",understanding_strategy="structured")
    async def policy(message, result):
        return "respond_only"
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid=client.post("/conversations").json()["id"]
        res=client.post(f"/conversations/{cid}/agent/messages",
            json={"content":"请建立计划：查询 RAG 然后计算 8*8，暂不执行",
                  "client_request_id":"e06-formal-0001"})
        assert res.status_code == 200,res.text
        assert res.json()["termination_reason"] == "plan_created_pending_execution"
        assert len(res.json()["plan"]) == 2
