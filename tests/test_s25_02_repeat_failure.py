"""Prevent repetitive deterministic tool failures without blocking changed arguments."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop


class DuplicateFailureThenAlternative:
    def __init__(self):
        self.index = 0

    async def decide(self, messages, tools):
        self.index += 1
        if self.index <= 3:
            expression = "1/0" if self.index <= 2 else "8*8"
            return Decision("", [{"id": str(self.index), "name": "calculate",
                                  "arguments": {"expression": expression}}])
        return Decision("64", [])


class RepeatedSuccess:
    def __init__(self):
        self.index = 0

    async def decide(self, messages, tools):
        self.index += 1
        if self.index <= 2:
            return Decision("", [{"id": str(self.index), "name": "calculate",
                                  "arguments": {"expression": "8*8"}}])
        return Decision("64", [])


def test_repeated_nonretryable_failure_is_rejected_and_alternative_runs():
    result = asyncio.run(AgentLoop(
        DuplicateFailureThenAlternative(), max_steps=6).run(
            "计算任务", include_evidence=True))
    observations = [x["observation"] for x in result["audit"]
                    if x["name"] == "calculate"]
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"
    assert observations[0]["error"] == "invalid_arguments"
    assert observations[1]["error"] == "repeated_nonretryable_action"
    assert observations[2]["ok"] is True
    assert observations[2]["value"] == 64


def test_successful_identical_read_only_call_is_not_blocked():
    result = asyncio.run(AgentLoop(
        RepeatedSuccess(), max_steps=5).run(
            "计算任务", include_evidence=True))
    observations = [x["observation"] for x in result["audit"]
                    if x["name"] == "calculate"]
    assert len(observations) == 2
    assert all(x["ok"] for x in observations)


def test_rejection_feedback_uses_tool_observation_shape():
    result = asyncio.run(AgentLoop(
        DuplicateFailureThenAlternative(), max_steps=6).run(
            "计算任务", include_evidence=True))
    trace = [t for t in result["trace"] if t["type"] == "tool"]
    assert [t["ok"] for t in trace] == [False, False, True]
    assert result["termination_reason"] == "unresolved_tool_failure"
