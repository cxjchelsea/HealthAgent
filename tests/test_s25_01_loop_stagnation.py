"""Repeated tool calls must terminate safely, not fabricate verified completion."""
import asyncio
from agent.loop import AgentLoop
from agent.decision import Decision
from agent.planning import PlanLedger


class SameDecision:
    async def decide(self, messages, tools):
        return Decision(content="", tool_calls=[{"id": "same", "name": "list_tasks", "arguments": {}}])


def test_identical_tool_request_stops_as_partial():
    loop = AgentLoop(SameDecision())
    state = {
        "iterations": 2, "status": "running", "memory_recall_required": False,
        "memory_recall_observed": True, "memory_recall_retry": False,
        "recall_retry_pending": False, "messages": [], "allowed_tools": [],
        "plan": PlanLedger(), "last_success": None,
        "superseded_step_ids": [], "superseded_expression": None,
        "traces": [], "last_tool_decision": '[{"arguments": {}, "name": "list_tasks"}]',
        "repeated_tool_decisions": 1,
    }
    output = asyncio.run(loop._decide(state))
    assert output["status"] == "partial"
    assert output["traces"][-1]["type"] == "repeat_tool_stop"
    assert "未验证完成" in output["final"]
