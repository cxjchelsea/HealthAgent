
"""D05 API regression: conflicting plan edit bypasses generic Understanding 503."""
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.plan_intent import PlanRequest
from agent.plan_revision_review import RevisionProposal
from apps.api.main import create_app
import pytest


def valid_understanding():
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "建一个只读计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None}
    })


def test_d05_conflicting_edit_returns_clarification_without_tools(tmp_path, monkeypatch):
    async def fake_complete(self, *, model, messages, response_schema=None):
        if response_schema and response_schema.get("title") == "RevisionProposal":
            return json.dumps({"intent": "revise", "step_number": 1,
                               "expected_tool": "calculate",
                               "old_argument": "8*8", "new_argument": "9*9"})
        return valid_understanding()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    async def fake_plan_intent(provider, model, message, existing_plan):
        return PlanRequest(intent="plan_only", steps=[
            {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
             "expected_arguments": {"term": "RAG"}},
            {"goal": "计算 8*8", "expected_tool": "calculate",
             "expected_arguments": {"expression": "8*8"}},
        ])
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_intent)
    async def policy(message, interpretation):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'d05.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        route = f"/conversations/{cid}/agent/messages"
        first = client.post(route, json={
            "content": "建立两步计划：先查询 RAG，再计算 8*8",
            "client_request_id": "d05-test-0001"})
        assert first.status_code == 200, first.text
        before = first.json()["plan_checkpoint"]
        revised = client.post(route, json={
            "content": "第一步不是计算 8*8，而是计算 9*9，其他保持不变",
            "client_request_id": "d05-test-0002"})
        assert revised.status_code == 200, revised.text
        body = revised.json()
        assert body["termination_reason"] == "plan_revision_conflict"
        assert body["status"] == "partial"
        assert body["steps"] == 0
        assert body["plan_checkpoint"] == before
        assert not any(step.get("type") == "tool" for step in body["trace"])


def test_d05_valid_second_step_edit_updates_revision(tmp_path, monkeypatch):
    async def fake_complete(self, *, model, messages, response_schema=None):
        if response_schema and response_schema.get("title") == "RevisionProposal":
            return json.dumps({"intent": "revise", "step_number": 2,
                               "expected_tool": "calculate",
                               "old_argument": "8*8", "new_argument": "9*9"})
        return valid_understanding()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    async def fake_plan_intent(provider, model, message, existing_plan):
        return PlanRequest(intent="plan_only", steps=[
            {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
             "expected_arguments": {"term": "RAG"}},
            {"goal": "计算 8*8", "expected_tool": "calculate",
             "expected_arguments": {"expression": "8*8"}},
        ])
    monkeypatch.setattr("apps.api.main.review_plan_intent", fake_plan_intent)
    async def policy(message, interpretation):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'d05ok.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        route = f"/conversations/{cid}/agent/messages"
        first = client.post(route, json={
            "content": "建立两步计划：先查询 RAG，再计算 8*8",
            "client_request_id": "d05-valid-0001"}).json()
        second = client.post(route, json={
            "content": "把第二步计算 8*8 改成 9*9，其他不变",
            "client_request_id": "d05-valid-0002"})
        assert second.status_code == 200, second.text
        result = second.json()
        assert result["termination_reason"] == "plan_revised_pending_execution"
        assert result["plan_revision"] == first["plan_revision"] + 1
        assert result["plan"][0] == first["plan"][0]
        assert result["plan"][1]["expected_arguments"]["expression"] == "9*9"
        assert result["steps"] == 0

# These API regression fixtures explicitly supply a formal saved-plan request.
# Stub only the new semantic admission review; the actual production model
# contract is tested separately in test_s25_02_f03_plan_admission.py.
@pytest.fixture(autouse=True)
def admit_fixture_saved_plan(monkeypatch):
    async def admitted(*args, **kwargs):
        return True
    monkeypatch.setattr("apps.api.main.review_plan_creation_reference", admitted)
