"""C01 proves the tool observation reaches the next model decision."""
import asyncio
import json
from agent.decision import Decision
from agent.loop import AgentLoop


class ReadExactError:
    def __init__(self):
        self.seen = False

    async def decide(self, messages, tools):
        observations = [m for m in messages if m["role"] == "tool"]
        if not observations:
            return Decision("", [{"id": "calc-01", "name": "calculate",
                                  "arguments": {"expression": "10/0"}}])
        observed = json.loads(observations[-1]["content"])
        assert observed["ok"] is False
        assert observed["error"] == "division_by_zero"
        assert observed["retryable"] is False
        assert "division by zero" in observed["message"]
        self.seen = True
        return Decision("工具确认该表达式存在除零，无法得到数值结果。", [])


def test_agent_receives_specific_arithmetic_failure():
    decider = ReadExactError()
    result = asyncio.run(AgentLoop(decider).run(
        "使用计算工具计算 10/0，如失败说明原因", include_evidence=True))
    assert decider.seen
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"
    assert result["audit"][0]["observation"]["error"] == "division_by_zero"
