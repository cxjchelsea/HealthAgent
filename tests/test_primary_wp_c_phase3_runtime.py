"""WP-C Phase 3 deterministic runtime regression checks."""
import asyncio
import json

from agent.decision import Decision
from agent.loop import AgentLoop
from agent.plan_intent import PlanRequest, build_plan_checkpoint
from agent.primary_route_v1 import RouteSignal


class Script:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    async def decide(self, messages, tools):
        return next(self.decisions)


def test_ordinary_readonly_does_not_complete_matching_saved_step():
    plan = build_plan_checkpoint("合成两步计划", PlanRequest(
        intent="plan_only", steps=[
            {"goal": "计算13+17", "expected_tool": "calculate",
             "expected_arguments": {"expression": "13+17"}},
            {"goal": "计算21+24", "expected_tool": "calculate",
             "expected_arguments": {"expression": "21+24"}}]))
    snapshot = json.loads(json.dumps(plan))
    model = Script([
        Decision("", [{"name": "calculate", "arguments": {"expression": "13+17"},
                       "id": "p3-calculation"}]),
        Decision("结果是30。", []),
    ])
    result = asyncio.run(AgentLoop(model).run(
        "在计划之外单独计算13+17", plan_checkpoint=plan,
        execution_mode="read_only"))
    assert result["status"] == "completed"
    assert result["termination_reason"] == "model_final"
    assert result["plan_checkpoint"] == snapshot
    assert [t["name"] for t in result["trace"] if t["type"] == "tool"] == ["calculate"]


def test_mcp_pending_field_rejects_tool_authorization():
    from pydantic import ValidationError
    import pytest
    base = {"schema_version": "primary-route-v1", "route_kind": "clarify",
            "quote": "先问我", "clarification_question": "查询什么？",
            "pending_read_tool_name": "mcp_reference_lookup"}
    RouteSignal.model_validate(base)
    with pytest.raises(ValidationError):
        RouteSignal.model_validate({
            **base, "route_kind": "ordinary_tool",
            "effect_intent": "read_only", "clarification_question": None})
    with pytest.raises(ValidationError):
        RouteSignal.model_validate({
            **base, "pending_write_tool_name": "create_task"})


def test_verified_pending_prompt_does_not_reclassify_reply():
    from agent.primary_route_v1 import _PRIMARY_SYSTEM
    assert "MUST be clarify_response" in _PRIMARY_SYSTEM
    assert "pending_read_tool_name=mcp_reference_lookup" in _PRIMARY_SYSTEM



def test_mcp_pending_origin_and_answer_cross_real_http_host(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from agent.config import Settings
    from agent.primary_route_v1 import bind_route_signal
    from apps.api.main import create_app
    from services.db import Turn, database
    from sqlalchemy import select

    origin = "请查询一个 MCP 术语，但我还没提供术语名称。"
    reply = "RAG"
    calls = []

    async def semantic(*args, **kwargs):
        message = args[2]
        calls.append(message)
        if message == origin:
            data = {
                "schema_version": "primary-route-v1",
                "route_kind": "clarify", "quote": "查询一个 MCP 术语",
                "clarification_question": "请提供要查询的术语。",
                "pending_read_tool_name": "mcp_reference_lookup",
            }
        else:
            assert message == reply
            assert kwargs["pending_action"] == "mcp_reference_lookup"
            assert kwargs["pending_origin_verified"] is True
            data = {"schema_version": "primary-route-v1",
                    "route_kind": "clarify_response", "quote": reply}
        return bind_route_signal(
            RouteSignal.model_validate(data), message,
            saved_plan_present=False,
            pending_action=kwargs["pending_action"],
            pending_origin_verified=kwargs["pending_origin_verified"])

    monkeypatch.setattr("apps.api.main.propose_primary_route", semantic)
    monkeypatch.setattr(
        "agent.primary_pending_readonly_v1.execute_tool",
        lambda name, arguments, tasks, **kwargs: {
            "ok": True, "term": arguments["term"], "definition": "reference"})
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'mcp_pending.db'}",
                   model_provider="fake", routing_profile="unified",
                   primary_route_v1_canary_enabled=True)
    with TestClient(create_app(cfg)) as client:
        cid = client.post("/conversations").json()["id"]
        endpoint = f"/conversations/{cid}/agent/messages"
        first = client.post(endpoint, json={
            "content": origin, "client_request_id": "mcp-origin-test"})
        assert first.status_code == 200
        assert first.json()["termination_reason"] == "needs_input"
        _, Session = database(cfg.database_url)
        with Session() as db:
            turn = db.scalar(select(Turn).where(Turn.request_id == "mcp-origin-test"))
            assert turn.failure_code == "needs_input:mcp_reference_lookup"
        result = client.post(endpoint, json={
            "content": reply, "client_request_id": "mcp-answer-test"})
        assert result.status_code == 200, result.text
        assert result.json()["termination_reason"] == "verified_clarification_execution"
        assert client.post(endpoint, json={
            "content": reply, "client_request_id": "mcp-answer-test"}).status_code == 409
    assert calls == [origin, reply]
