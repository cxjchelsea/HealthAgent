"""S25-02 Step 4 slice: typed termination causes on the single AgentLoop."""
import asyncio

from agent.decision import Decision, ToolDecisionError
from agent.loop import AgentLoop


class FinalDecision:
    async def decide(self, messages, tools):
        return Decision(content="Hello", tool_calls=[])


class OverFanout:
    async def decide(self, messages, tools):
        return Decision(content="", tool_calls=[
            {"id": str(i), "name": "calculate", "arguments": {"expression": "1+1"}}
            for i in range(5)
        ])


class SingleTool:
    async def decide(self, messages, tools):
        return Decision(content="", tool_calls=[
            {"id": "a", "name": "calculate", "arguments": {"expression": "1+1"}}])


class Unavailable:
    async def decide(self, messages, tools):
        raise ToolDecisionError("empty_decision")


def _run(decider, **kwargs):
    return asyncio.run(AgentLoop(decider, **kwargs).run("请帮我处理这个任务"))


def test_direct_answer_has_explicit_completion_cause():
    result = _run(FinalDecision())
    assert result["status"] == "completed"
    assert result["termination_reason"] == "model_final"


def test_pending_checkpoint_has_specific_partial_cause():
    checkpoint = {"revision": 1, "steps": [
        {"id": 1, "goal": "Future work", "status": "pending"}]}
    result = asyncio.run(AgentLoop(FinalDecision()).run(
        "继续处理任务", plan_checkpoint=checkpoint))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "pending_plan_steps"


def test_tool_fanout_limit_is_not_misreported_as_completion():
    result = _run(OverFanout())
    assert result["status"] == "tool_limit"
    assert result["termination_reason"] == "tool_fanout_exceeded"


def test_decision_budget_limit_is_explicit():
    result = _run(SingleTool(), max_steps=1)
    assert result["status"] == "step_limit"
    assert result["termination_reason"] == "decision_budget_exhausted"


def test_provider_empty_decision_has_typed_reason():
    result = _run(Unavailable())
    assert result["status"] == "provider_error"
    assert result["termination_reason"] == "empty_decision"
    assert result["failure_code"] == "empty_decision"
