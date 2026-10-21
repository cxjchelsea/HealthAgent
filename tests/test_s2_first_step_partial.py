"""S2 partial-plan termination requires verified first-step evidence."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop


class FirstOnlyPlanner:
    def __init__(self): self.calls = 0
    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "p", "name": "propose_plan",
                "arguments": {"steps": [
                    {"goal": "calculate 8*8", "expected_tool": "calculate",
                     "expected_arguments": {"expression": "8*8"}},
                    {"goal": "lookup RAG", "expected_tool": "mcp_reference_lookup",
                     "expected_arguments": {"term": "rag"}},
                    {"goal": "calculate 9*9", "expected_tool": "calculate",
                     "expected_arguments": {"expression": "9*9"}}]}}])
        return Decision("", [{"id": "c", "name": "calculate",
                             "arguments": {"expression": "8*8"}}])


def test_first_step_only_persists_partial_without_more_model_calls():
    model = FirstOnlyPlanner()
    outcome = asyncio.run(AgentLoop(model).run(
        "请创建三个步骤的计划：计算8*8、查RAG、计算9*9。先尝试完成第一步，剩余步骤保留。",
        include_evidence=True))
    assert outcome["status"] == "partial"
    assert model.calls == 2
    assert [s["status"] for s in outcome["plan"]] == [
        "completed", "pending", "pending"]
    assert [s["status"] for s in outcome["plan_checkpoint"]["steps"]] == [
        "completed", "pending", "pending"]
    assert any(x["type"] == "partial_first_step_verified" for x in outcome["trace"])


def test_non_bounded_request_does_not_early_stop():
    model = FirstOnlyPlanner()
    outcome = asyncio.run(AgentLoop(model, max_steps=4).run(
        "创建三个步骤的计划：计算8*8、查RAG、计算9*9，并完成全部步骤。"))
    assert outcome["status"] == "step_limit"
    assert model.calls == 4


class UnboundPlanner(FirstOnlyPlanner):
    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "p", "name": "propose_plan",
                                   "arguments": {"steps": ["first", "second", "third"]}}])
        return Decision("", [{"id": "c", "name": "calculate",
                             "arguments": {"expression": "8*8"}}])


def test_unbound_tool_success_is_not_verification():
    model = UnboundPlanner()
    outcome = asyncio.run(AgentLoop(model, max_steps=4).run(
        "创建三个步骤的计划。先尝试完成第一步，剩余步骤保留。"))
    assert outcome["status"] == "step_limit"
    assert outcome["plan"][0]["status"] == "pending"
