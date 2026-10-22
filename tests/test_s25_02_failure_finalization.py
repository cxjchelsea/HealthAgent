"""S25-02: an unresolved tool failure cannot be finalized as success."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop


class FailThenClaim:
    async def decide(self, messages, tools):
        if not any(m["role"] == "tool" for m in messages):
            return Decision("", [{"id": "bad", "name": "calculate",
                                  "arguments": {"expression": "1/0"}}])
        return Decision("已经成功计算，结果为 5。", [])


class FailThenRepair:
    async def decide(self, messages, tools):
        tool_messages = [m for m in messages if m["role"] == "tool"]
        if not tool_messages:
            expression = "1/0"
        elif len(tool_messages) == 1:
            expression = "10/2"
        else:
            return Decision("已计算：5", [])
        return Decision("", [{"id": str(len(tool_messages)), "name": "calculate",
                              "arguments": {"expression": expression}}])


class DirectAnswer:
    async def decide(self, messages, tools):
        return Decision("你好", [])


def test_failed_tool_then_unverified_success_claim_stays_partial():
    result = asyncio.run(AgentLoop(FailThenClaim()).run("计算任务", include_evidence=True))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"
    assert "不构成执行成功证明" in result["answer"]
    assert result["audit"][0]["observation"]["ok"] is False


def test_different_arguments_do_not_clear_failed_action():
    result = asyncio.run(AgentLoop(FailThenRepair()).run("计算任务", include_evidence=True))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"
    assert [a["observation"]["ok"] for a in result["audit"]] == [False, True]


def test_plain_answer_remains_completed():
    result = asyncio.run(AgentLoop(DirectAnswer()).run("你好"))
    assert result["status"] == "completed"
    assert result["termination_reason"] == "model_final"
