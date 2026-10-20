"""S2-D evidence-bound planning and recovery regressions."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop
from agent.planning import PlanLedger


def bound(goal, tool, args):
    return {"goal": goal, "expected_tool": tool, "expected_arguments": args}


def test_only_exact_unique_bound_observations_complete_step():
    ledger = PlanLedger()
    assert ledger.propose([bound("calculate 8*8", "calculate", {"expression": "8*8"})])["ok"]
    ledger.observe("calculate", {"ok": True, "value": 64}, {"expression": "8*7"})
    assert ledger.steps[0]["status"] == "pending"
    ledger.observe("calculate", {"ok": False, "error": "invalid_arguments"}, {"expression": "8*8"})
    assert ledger.steps[0]["status"] == "pending"
    ledger.observe("calculate", {"ok": True, "value": 64}, {"expression": "8*8"})
    assert ledger.steps[0]["status"] == "completed"
    assert ledger.events[-1]["source"] == "verified_binding"


def test_ambiguous_evidence_never_autocompletes_multiple_steps():
    ledger = PlanLedger()
    assert ledger.propose([bound("first", "calculate", {"expression": "8*8"}),
                           bound("second", "calculate", {"expression": "8*8"})])["ok"]
    ledger.observe("calculate", {"ok": True, "value": 64}, {"expression": "8*8"})
    assert all(x["status"] == "pending" for x in ledger.steps)


class RecoveryDecider:
    def __init__(self):
        self.i = 0

    async def decide(self, messages, tools):
        self.i += 1
        calls = {
            1: ("propose_plan", {"steps": [
                bound("lookup RAG", "mcp_reference_lookup", {"term": "rag"}),
                bound("calculate 8*8", "calculate", {"expression": "8*8"})]}),
            2: ("mcp_reference_lookup", {"term": "rag"}),
            3: ("calculate", {"expression": "8/0"}),
            4: ("revise_plan", {"reason": "correct failed calculation",
                               "pending_steps": [bound("retry 8*8", "calculate",
                                                       {"expression": "8*8"})]}),
            5: ("calculate", {"expression": "8*8"}),
        }
        if self.i not in calls:
            return Decision("RAG已查询，64", [])
        name, args = calls[self.i]
        return Decision("", [{"id": f"c-{self.i}", "name": name, "arguments": args}])


def test_agentloop_evidence_and_local_replanning_e2e():
    class MCP:
        def call(self, name, arguments):
            return {"ok": True, "term": "rag", "definition": "retrieval"}
    result = asyncio.run(AgentLoop(RecoveryDecider(), max_steps=8, mcp_client=MCP()).run(
        "制定计划，查询 RAG，再计算 8*8，失败后重新规划", include_evidence=True))
    assert result["status"] == "completed"
    assert result["plan_revision"] == 2
    assert len(result["plan"]) == 2
    assert all(x["status"] == "completed" for x in result["plan"])
    assert any(x["event"] == "tool_failed" for x in result["plan_events"])
    assert any(x["event"] == "plan_revised" and x["preserved"] == 1
               for x in result["plan_events"])
