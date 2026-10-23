import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop

class RecoveringDecider:
    def __init__(self):
        self.calls = 0
    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "one", "name": "calculate", "arguments": {"expression": "1/0"}}])
        if self.calls == 2:
            assert '"ok": false' in messages[-1]["content"]
            return Decision("", [{"id": "two", "name": "calculate", "arguments": {"expression": "10/2"}}])
        assert '"value": 5.0' in messages[-1]["content"]
        return Decision("答案是5", [])

def test_model_corrects_tool_failure_using_observation():
    result = asyncio.run(AgentLoop(RecoveringDecider()).run("修复计算错误后得到结果"))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"
    tools = [x for x in result["trace"] if x["type"] == "tool"]
    assert [x["ok"] for x in tools] == [False, True]
    assert result["steps"] == 3
