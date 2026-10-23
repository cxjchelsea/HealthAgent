"""D05-F02: an execution request after revision must not go through revise."""
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.plan_intent import PlanRequest
from agent.plan_revision_review import RevisionProposal
from apps.api.main import create_app


def understood():
    return json.dumps({"schema_version":"s25-01-v1","status":"understood",
        "goal":{"objective":"执行保存的步骤","subgoals":[],"constraints":[],
                "success_criteria":[]},
        "change":{"kind":"new_goal","target_goal_id":None,"delta":None},
        "clarification":{"required":False,"reason_code":"none","question":None}})


def test_d05_execution_priority_after_revision(tmp_path, monkeypatch):
    async def fake_complete(self, *, model, messages, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    calls = []
    async def plan_intent(provider, model, message, existing_plan):
        if not existing_plan:
            return PlanRequest(intent="plan_only", steps=[
                {"goal":"查询 RAG","expected_tool":"mcp_reference_lookup",
                 "expected_arguments":{"term":"RAG"}},
                {"goal":"计算 8*8","expected_tool":"calculate",
                 "expected_arguments":{"expression":"8*8"}}])
        if "执行第一步" in message:
            return PlanRequest(intent="execute_first")
        return PlanRequest(intent="other")
    async def revision(provider, model, message, checkpoint):
        calls.append(message)
        return RevisionProposal(intent="revise", step_number=2,
                                expected_tool="calculate",old_argument="8*8",
                                new_argument="9*9")
    monkeypatch.setattr("apps.api.main.review_plan_intent", plan_intent)
    monkeypatch.setattr("apps.api.main.review_plan_revision", revision)
    async def policy(message, interpretation):
        return "read_only"
    settings=Settings(database_url=f"sqlite:///{tmp_path/'d05f02.db'}",
                      model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid=client.post("/conversations").json()["id"]
        route=f"/conversations/{cid}/agent/messages"
        def send(content, request_id):
            res=client.post(route,json={"content":content,
                                        "client_request_id":request_id})
            assert res.status_code == 200, res.text
            return res.json()
        first=send("建立计划：查询 RAG，计算 8*8","d05f02-001")
        assert first["plan_revision"] == 1
        revised=send("请将第二步计算 8*8 修改成 9*9，不要执行","d05f02-002")
        assert revised["termination_reason"] == "plan_revised_pending_execution"
        assert revised["plan_revision"] == 2
        assert revised["steps"] == 0
        count=len(calls)
        executed=send("现在请帮我执行第一步","d05f02-003")
        assert len(calls) == count, "Execution request entered revision classifier"
        assert executed["termination_reason"] != "plan_revision_conflict"
        assert executed["plan"][0]["status"] == "completed"
        assert executed["plan"][1]["status"] == "pending"
        assert executed["plan"][1]["expected_arguments"]["expression"] == "9*9"
        assert len([event for event in executed["trace"] if event.get("type") == "tool"]) == 1
