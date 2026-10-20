"""Regression: invalid step IDs do not mutate the active plan or bypass retry."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


class WrongIdThenCorrect:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        args = {
            "pending_steps": [{
                "goal": "Corrected calculation", "expected_tool": "calculate",
                "expected_arguments": {"expression": "9*9"}}],
            "reason": "User replaced the calculation",
            "invalidate_step_ids": [99] if self.calls == 1 else [1],
        }
        if self.calls == 2:
            assert '"missing_required_invalidation"' in messages[-1]["content"]
            assert '"plan_revision": 1' in messages[-1]["content"]
        if self.calls <= 2:
            return Decision("", [{"id": f"revision-{self.calls}",
                                  "name": "revise_plan", "arguments": args}])
        if self.calls == 3:
            return Decision("", [{"id": "calculation", "name": "calculate",
                                  "arguments": {"expression": "9*9"}}])
        return Decision("done", [])


def test_wrong_invalidation_id_rejected_before_plan_mutation():
    checkpoint = {"revision": 1, "steps": [{
        "id": 1, "goal": "Original calculation", "status": "completed",
        "expected_tool": "calculate",
        "expected_arguments": {"expression": "8*8"}, "evidence_tool": "calculate"}]}
    result = asyncio.run(AgentLoop(WrongIdThenCorrect(), max_steps=7).run(
        "更正目标：不是8*8，而是9*9。", plan_checkpoint=checkpoint,
        include_evidence=True))
    assert result["status"] == "completed"
    assert result["plan_revision"] == 2
    assert len(result["plan"]) == 1
    assert result["plan"][0]["expected_arguments"] == {"expression": "9*9"}
    attempts = [x for x in result["audit"] if x["name"] == "revise_plan"]
    assert len(attempts) == 2
    assert attempts[0]["observation"]["ok"] is False
    assert attempts[0]["observation"]["required_step_ids"] == [1]
    assert attempts[1]["observation"]["ok"] is True
    assert [x["invalidated"] for x in result["plan_events"]
            if x["event"] == "plan_revised"] == [[1]]
