"""S25-02 model-selected clarification: no keyword router or side effects."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


class Ask:
    async def decide(self, messages, tools):
        assert "request_clarification" in {x["function"]["name"] for x in tools}
        return Decision("", [{"id": "clarify", "name": "request_clarification",
                              "arguments": {"question": "你希望查询哪个词条？"}}])


class Mixed:
    async def decide(self, messages, tools):
        return Decision("", [
            {"id": "a", "name": "request_clarification",
             "arguments": {"question": "请选择词条"}},
            {"id": "b", "name": "create_task",
             "arguments": {"title": "must-not-run"}}])


def test_model_can_request_clarification_without_keyword_routing():
    outcome = asyncio.run(AgentLoop(Ask()).run("查一下资料", include_evidence=True))
    assert outcome["status"] == "partial"
    assert outcome["termination_reason"] == "needs_input"
    assert outcome["answer"] == "你希望查询哪个词条？"
    assert outcome["plan_checkpoint"] is None
    assert any(x["type"] == "clarification_requested" for x in outcome["trace"])


def test_clarification_with_side_effect_is_rejected_as_a_batch():
    # A bad multi-action decision must not execute the write tool.
    class BadThenStop:
        def __init__(self):
            self.calls = 0

        async def decide(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return await Mixed().decide(messages, tools)
            assert "control_action_must_be_alone" in messages[-1]["content"]
            return Decision("请明确查询对象。", [])

    result = asyncio.run(AgentLoop(BadThenStop()).run(
        "查一下资料", include_evidence=True))
    assert result["status"] == "completed"
    assert not any(item["name"] == "create_task" for item in result["audit"])


def test_invalid_clarification_arguments_return_tool_feedback():
    class MalformedThenStop:
        def __init__(self):
            self.calls = 0

        async def decide(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return Decision("", [{"id": "a", "name": "request_clarification",
                                      "arguments": {"question": ""}}])
            assert "invalid_arguments" in messages[-1]["content"]
            return Decision("请说明你想查找的词条。", [])

    result = asyncio.run(AgentLoop(MalformedThenStop()).run("查询资料"))
    assert result["status"] == "completed"
