"""Real-report root cause regressions through the production API Host."""
import json

from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from agent.execution_policy import ExecutionPolicyDecision
from agent.plan_intent import PlanRequest
from agent.plan_revision_review import RevisionProposal
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


def understood(*, clarify=False):
    return json.dumps({
        "schema_version": "s25-01-v1",
        "status": "clarification_required" if clarify else "understood",
        "goal": None if clarify else {"objective": "执行当前请求",
             "subgoals": [], "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": clarify,
            "reason_code": "missing_critical_constraint" if clarify else "none",
            "question": "您想查询的词条是什么？" if clarify else None},
    }, ensure_ascii=False)


def test_f03_pending_reference_term_round_trip(tmp_path, monkeypatch):
    async def complete(self, *, messages, model, response_schema=None):
        properties = (response_schema or {}).get("properties", {})
        if "explicitly_requests_mcp_lookup_without_term" in properties:
            return '{"explicitly_requests_mcp_lookup_without_term":true}'
        if "kind" in properties and "schema_version" not in properties:
            return '{"kind":"answer"}'
        return understood(clarify=True)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", complete)
    settings = Settings(database_url=f"sqlite:///{tmp_path/'f03.db'}",
                        model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        path = f"/conversations/{cid}/agent/messages"
        first = client.post(path, json={
            "content": "请在 MCP 资料中查一个词条，但还没说词条。",
            "client_request_id": "f03-new-001"})
        assert first.status_code == 200, first.text
        assert first.json()["pending_action"] == "mcp_reference_lookup"
        second = client.post(path, json={
            "content": "RAG", "client_request_id": "f03-new-002"})
        assert second.status_code == 200, second.text
        assert second.json()["termination_reason"] == "verified_clarification_execution"
        assert any(x.get("name") == "mcp_reference_lookup" and x.get("ok")
                   for x in second.json()["trace"])


def test_e06_wrong_plan_intent_does_not_intercept_standalone_tool(tmp_path, monkeypatch):
    async def complete(self, *, messages, model, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", complete)
    calls = []
    async def intent(provider, model, message, existing_plan):
        if not existing_plan:
            return PlanRequest(intent="plan_only", steps=[
                {"goal":"查询 RAG", "expected_tool":"mcp_reference_lookup",
                 "expected_arguments":{"term":"RAG"}},
                {"goal":"计算 8*8", "expected_tool":"calculate",
                 "expected_arguments":{"expression":"8*8"}}])
        return PlanRequest(intent="execute_first")  # deliberate hallucination
    async def creation(*args, **kwargs):
        return True
    async def revision(*args, **kwargs):
        return RevisionProposal(intent="other", edit_requested=False)
    async def policy(*args, **kwargs):
        return ExecutionPolicyDecision(mode="read_only", target="tool_action")
    monkeypatch.setattr("apps.api.main.review_plan_intent", intent)
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", creation)
    monkeypatch.setattr("apps.api.main.review_plan_revision", revision)
    monkeypatch.setattr("apps.api.main.decide_execution_policy_detail", policy)

    class Decider:
        async def decide(self, messages, tools):
            calls.append(1)
            if len(calls) == 1:
                return Decision("", [{"id":"c1", "name":"calculate",
                                     "arguments":{"expression":"19+23"}}])
            return Decision("结果是42", [])
    monkeypatch.setattr("apps.api.main.NativeToolDecision", lambda settings: Decider())
    settings = Settings(database_url=f"sqlite:///{tmp_path/'e06.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings)) as client:
        cid = client.post("/conversations").json()["id"]
        path = f"/conversations/{cid}/agent/messages"
        plan = client.post(path, json={
            "content":"请建立两步正式计划，先查询 RAG，再计算 8*8，暂不执行",
            "client_request_id":"e06-new-001"})
        assert plan.status_code == 200, plan.text
        assert plan.json()["termination_reason"] == "plan_created_pending_execution"
        standalone = client.post(path, json={
            "content":"请单独使用计算工具计算 19+23，不要执行刚才的计划。",
            "client_request_id":"e06-new-002"})
        assert standalone.status_code == 200, standalone.text
        assert not any(x.get("type") == "plan_resume_execution"
                       for x in standalone.json()["trace"])
        assert any(x.get("name") == "calculate" for x in standalone.json()["trace"])
        assert [x["status"] for x in standalone.json()["plan"]] == ["pending","pending"]
