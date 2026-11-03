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
