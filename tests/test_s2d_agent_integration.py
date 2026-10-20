"""S2-D whole AgentLoop execution using model-shaped tool calls."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop


class ScriptedPlanner:
    def __init__(self):
        self.calls = 0
    async def decide(self, messages, tools):
        self.calls += 1
        name = {1: "propose_plan", 2: "calculate", 3: "complete_plan_step"}.get(self.calls)
        if name is None:
            return Decision("已完成核算，结果是64。", [])
        args = {
            "propose_plan": {"steps": ["calculate 8*8"]},
            "calculate": {"expression": "8*8"},
            "complete_plan_step": {"step_id": 1, "evidence_tool": "calculate"},
        }[name]
        return Decision("", [{"id": f"plan-{self.calls}", "name": name, "arguments": args}])


def test_existing_agent_loop_runs_explicit_plan_and_evidence():
    result = asyncio.run(AgentLoop(ScriptedPlanner()).run(
        "请制定计划，再计算 8*8 并完成计划", include_evidence=True))
    assert result["status"] == "completed"
    assert result["plan_revision"] == 1
    assert result["plan"][0]["status"] == "completed"
    assert result["plan"][0]["evidence_tool"] == "calculate"
    assert [item["name"] for item in result["audit"]] == [
        "propose_plan", "calculate", "complete_plan_step"]


class FakeCompletion:
    def __init__(self):
        self.calls = 0
    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "a", "name": "propose_plan",
                                  "arguments": {"steps": ["unverified work"]}}])
        if self.calls == 2:
            return Decision("", [{"id": "b", "name": "complete_plan_step",
                                  "arguments": {"step_id": 1, "evidence_tool": "calculate"}}])
        return Decision("未获得执行证据", [])


def test_agent_loop_rejects_completion_without_observation():
    result = asyncio.run(AgentLoop(FakeCompletion()).run(
        "规划一个多步任务，只有执行后才能完成", include_evidence=True))
    assert result["status"] == "completed"
    assert result["plan"][0]["status"] == "pending"
    assert result["audit"][-1]["observation"]["error"] == "missing_success_evidence"
