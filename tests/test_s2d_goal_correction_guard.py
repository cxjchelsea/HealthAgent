"""Goal correction guard: replacement is not an additive follow-up."""
import asyncio

from agent.decision import Decision
from agent.loop import AgentLoop


class CorrectionDecider:
    async def decide(self, messages, tools):
        return Decision(content="done", tool_calls=[])


def _checkpoint():
    return {"revision": 1, "steps": [{
        "id": 1, "goal": "Calculate 8*8", "status": "completed",
        "expected_tool": "calculate",
        "expected_arguments": {"expression": "8*8"},
        "evidence_tool": "calculate"}]}


def _run(goal, checkpoint):
    return asyncio.run(AgentLoop(CorrectionDecider()).run(
        goal, plan_checkpoint=checkpoint))


def test_replaced_completed_calculation_cannot_claim_completed():
    result = _run("更正目标：不是8*8，而是9*9。", _checkpoint())
    assert result["status"] == "partial"
    assert "旧步骤" in result["answer"]


def test_additive_follow_up_preserves_completed_evidence():
    result = _run("在之前的8*8基础上，再算9*9。", _checkpoint())
    assert result["status"] == "completed"


def test_unrelated_correction_does_not_invalidate_arbitrary_steps():
    result = _run("更正目标：不是7*7，而是9*9。", _checkpoint())
    assert result["status"] == "completed"


def test_replacement_guard_does_not_reject_fresh_plan():
    result = _run("不是8*8，而是9*9。", None)
    assert result["status"] == "completed"
