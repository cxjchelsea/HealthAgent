"""A rejected stale revision must be corrected by the model, not the executor."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


class RetryCorrection:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            args = {
                "pending_steps": [{
                    "goal": "Calculate 9*9", "expected_tool": "calculate",
                    "expected_arguments": {"expression": "9*9"}}],
                "reason": "User replaced prior calculation",
                "invalidate_step_ids": [],
            }
            return Decision("", [{"id": "rev1", "name": "revise_plan", "arguments": args}])
        if self.calls == 2:
            observation = messages[-1]
            assert observation["role"] == "tool"
            assert "missing_required_invalidation" in observation["content"]
            args = {
                "pending_steps": [{
                    "goal": "Calculate 9*9", "expected_tool": "calculate",
                    "expected_arguments": {"expression": "9*9"}}],
                "reason": "Replace superseded calculation",
                "invalidate_step_ids": [1],
            }
            return Decision("", [{"id": "rev2", "name": "revise_plan", "arguments": args}])
        if self.calls == 3:
            return Decision("", [{"id": "calc", "name": "calculate",
                                  "arguments": {"expression": "9*9"}}])
        return Decision("Result verified", [])


def test_revision_retry_requires_explicit_invalidation():
    checkpoint = {"revision": 1, "steps": [{
        "id": 1, "goal": "Calculate 8*8", "status": "completed",
        "expected_tool": "calculate",
        "expected_arguments": {"expression": "8*8"}, "evidence_tool": "calculate"}]}
    result = asyncio.run(AgentLoop(RetryCorrection(), max_steps=7).run(
        "更正目标：不是8*8，而是9*9。", plan_checkpoint=checkpoint, include_evidence=True))
    assert result["status"] == "completed"
    assert result["plan_revision"] == 2
    assert len(result["plan"]) == 1
    assert result["plan"][0]["expected_arguments"] == {"expression": "9*9"}
    assert result["plan"][0]["status"] == "completed"
    rejected = [x for x in result["audit"] if x["name"] == "revise_plan" and
                x["observation"].get("error") == "missing_required_invalidation"]
    assert len(rejected) == 1
    assert rejected[0]["observation"]["plan_revision"] == 1
    assert any(x.get("event") == "plan_revised" and x.get("invalidated") == [1]
               for x in result["plan_events"])
