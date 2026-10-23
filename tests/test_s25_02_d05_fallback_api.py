"""D05-F01 fallback is fail-closed and does not affect execution."""
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.understanding import HTTPUnderstandingModel
from agent.plan_intent import PlanRequest
from agent.plan_revision_review import RevisionProposal
from apps.api.main import create_app


def understood():
    return json.dumps({
        "schema_version": "s25-01-v1", "status": "understood",
        "goal": {"objective": "执行已有计划", "subgoals": [],
                 "constraints": [], "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None}
    })


def test_unresolved_revision_returns_conflict_and_preserves_checkpoint(tmp_path, monkeypatch):
    async def fake_complete(self, *, model, messages, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)

    async def plan_intent(provider, model, message, existing_plan):
        if not existing_plan:
            return PlanRequest(intent="plan_only", steps=[
                {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
                 "expected_arguments": {"term": "RAG"}},
                {"goal": "计算 8*8", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "8*8"}}])
        return PlanRequest(intent="other")
    async def revision(provider, model, message, checkpoint):
        return RevisionProposal(intent="other")
    async def edit_check(provider, model, message, checkpoint):
        return True
    monkeypatch.setattr("apps.api.main.review_plan_intent", plan_intent)
    monkeypatch.setattr("apps.api.main.review_plan_revision", revision)
    monkeypatch.setattr("apps.api.main.check_plan_edit_intent", edit_check)
    async def policy(message, interpretation):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'d05fallback.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        url = f"/conversations/{cid}/agent/messages"
        first = client.post(url, json={
            "content": "建立计划：查询 RAG 再计算 8*8",
            "client_request_id": "d05-fallback-001"})
        assert first.status_code == 200, first.text
        checkpoint = first.json()["plan_checkpoint"]
        second = client.post(url, json={
            "content": "第一步不是计算 8*8 而是 9*9",
            "client_request_id": "d05-fallback-002"})
        assert second.status_code == 200, second.text
        out = second.json()
        assert out["status"] == "partial"
        assert out["termination_reason"] == "plan_revision_conflict"
        assert out["steps"] == 0
        assert out["plan_checkpoint"] == checkpoint
        assert out["trace"] == [{"step": 0, "type": "plan_revision_conflict"}]


def test_execution_intent_skips_revision_fallback(tmp_path, monkeypatch):
    async def fake_complete(self, *, model, messages, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    async def plan_intent(provider, model, message, existing_plan):
        if not existing_plan:
            return PlanRequest(intent="plan_only", steps=[
                {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
                 "expected_arguments": {"term": "RAG"}}])
        return PlanRequest(intent="execute_first")
    async def should_not_run(*args):
        raise AssertionError("execution request entered revision fallback")
    monkeypatch.setattr("apps.api.main.review_plan_intent", plan_intent)
    monkeypatch.setattr("apps.api.main.review_plan_revision", should_not_run)
    monkeypatch.setattr("apps.api.main.check_plan_edit_intent", should_not_run)
    async def policy(message, interpretation):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'d05exec.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        url = f"/conversations/{cid}/agent/messages"
        created = client.post(url, json={
            "content": "建立计划：查询 RAG", "client_request_id": "d05-exec-001"})
        assert created.status_code == 200, created.text
        executed = client.post(url, json={
            "content": "现在执行第一步", "client_request_id": "d05-exec-002"})
        assert executed.status_code == 200, executed.text
        assert executed.json()["plan"][0]["status"] == "completed"


def test_exact_d05_conflict_short_circuits_all_models(tmp_path, monkeypatch):
    async def fake_complete(self, *, model, messages, response_schema=None):
        return understood()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)

    async def plan_intent(provider, model, message, existing_plan):
        if not existing_plan:
            return PlanRequest(intent="plan_only", steps=[
                {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
                 "expected_arguments": {"term": "RAG"}},
                {"goal": "计算 8*8", "expected_tool": "calculate",
                 "expected_arguments": {"expression": "8*8"}}])
        raise AssertionError("Contradictory edit must be gated before model routing")
    monkeypatch.setattr("apps.api.main.review_plan_intent", plan_intent)
    async def policy(message, interpretation):
        return "read_only"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'d05exact.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        url = f"/conversations/{cid}/agent/messages"
        first = client.post(url, json={
            "content": "建立计划：第一步查询 RAG，第二步计算 8*8",
            "client_request_id": "d05-exact-001"})
        assert first.status_code == 200, first.text
        cp = first.json()["plan_checkpoint"]
        response = client.post(url, json={
            "content": "第一步不是计算 8*8，而是计算 9*9，其他步骤保持不变。",
            "client_request_id": "d05-exact-002"})
        assert response.status_code == 200, response.text
        out = response.json()
        assert out["termination_reason"] == "plan_revision_conflict"
        assert out["steps"] == 0
        assert out["plan_checkpoint"] == cp


def test_deterministic_precheck_has_narrow_scope():
    from agent.plan_revision_review import explicit_step_binding_conflict
    from agent.plan_intent import build_plan_checkpoint
    cp = build_plan_checkpoint("查询 RAG 并计算 8*8", PlanRequest(
        intent="plan_only", steps=[
            {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
             "expected_arguments": {"term": "RAG"}},
            {"goal": "计算 8*8", "expected_tool": "calculate",
             "expected_arguments": {"expression": "8*8"}}]))
    assert explicit_step_binding_conflict(
        "第一步不是计算 8*8，而是计算 9*9，其他步骤保持不变。", cp)
    assert not explicit_step_binding_conflict("现在执行第一步", cp)
    assert not explicit_step_binding_conflict("请把第二步计算 8*8 修改为 9*9", cp)
    assert not explicit_step_binding_conflict("什么是 8*8 和 9*9？", cp)
