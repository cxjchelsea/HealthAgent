"""S25-01 boundary: interpretation may remove but never grant tool authority."""
import asyncio
from agent.loop import AgentLoop
from agent.decision import Decision

class Decider:
    def __init__(self):
        self.tools_seen = None
    async def decide(self, messages, tools):
        self.tools_seen = tools
        return Decision(content="这是一份不保存的文字草案。", tool_calls=[])

def test_response_only_goals_are_not_offered_tools():
    decider = Decider()
    result = asyncio.run(AgentLoop(decider).run(
        "请给我三项草案，不要保存",
        understanding_result={"execution_intent": "respond_only",
                              "change": {"kind": "new_goal"}}))
    assert decider.tools_seen == []
    assert result["status"] == "completed"
    assert result["plan_revision"] == 0

def test_read_only_intent_does_not_strip_all_tools():
    decider = Decider()
    result = asyncio.run(AgentLoop(decider).run(
        "计算8*8", understanding_result={"execution_intent": "read_only",
                                    "change": {"kind": "new_goal"}}))
    assert any(x["function"]["name"] == "calculate" for x in decider.tools_seen)
    assert result["status"] == "completed"
