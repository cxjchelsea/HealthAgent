"""S25-01 + S25-02 integrated contract checks on current main."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop


class InspectDecider:
    def __init__(self, decision):
        self.decision = decision
        self.visible = []

    async def decide(self, messages, tools):
        self.visible.append({x["function"]["name"] for x in tools})
        return self.decision


def _interpretation():
    return {"schema_version": "s25-01-v1", "status": "understood",
            "goal": {"objective": "Explain this", "subgoals": [],
                     "constraints": [], "success_criteria": []},
            "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None}}


def test_respond_only_does_not_expose_any_tools():
    decider = InspectDecider(Decision("Here is the explanation.", []))
    result = asyncio.run(AgentLoop(decider).run(
        "Explain this", understanding_result=_interpretation(),
        execution_mode="respond_only"))
    assert result["status"] == "completed"
    assert decider.visible == [set()]


def test_read_only_mode_keeps_clarification_and_blocks_write():
    decider = InspectDecider(Decision("Let me explain.", []))
    asyncio.run(AgentLoop(decider).run(
        "Explain this", understanding_result=_interpretation(),
        execution_mode="read_only"))
    assert "request_clarification" in decider.visible[0]
    assert "create_task" not in decider.visible[0]
    assert "save_memory" not in decider.visible[0]


class Clarify:
    async def decide(self, messages, tools):
        return Decision("", [{"id": "c1", "name": "request_clarification",
                              "arguments": {"question": "Which reference term?"}}])


def test_model_clarification_returns_partial_in_structured_mode():
    result = asyncio.run(AgentLoop(Clarify()).run(
        "Search reference", understanding_result=_interpretation(),
        execution_mode="read_only"))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "needs_input"
    assert result["answer"] == "Which reference term?"


class FailThenClaim:
    async def decide(self, messages, tools):
        if not any(m["role"] == "tool" for m in messages):
            return Decision("", [{"id": "c", "name": "calculate",
                                  "arguments": {"expression": "8/0"}}])
        return Decision("The calculation succeeded.", [])


def test_structured_loop_cannot_claim_success_after_tool_failure():
    result = asyncio.run(AgentLoop(FailThenClaim()).run(
        "Calculate", understanding_result=_interpretation(),
        execution_mode="read_only"))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"


class UnauthorizedClarification:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        assert not tools  # respond_only must continue exposing zero tools
        if self.calls == 1:
            return Decision("", [{"id": "unauthorized", "name": "request_clarification",
                                  "arguments": {"question": "Should not be accepted"}}])
        assert "tool_not_exposed" in messages[-1]["content"]
        return Decision("Only an explanation, no action.", [])


def test_unexposed_clarification_control_cannot_bypass_respond_only():
    decider = UnauthorizedClarification()
    result = asyncio.run(AgentLoop(decider).run(
        "Explain this", understanding_result=_interpretation(),
        execution_mode="respond_only"))
    assert decider.calls == 2
    assert result["status"] == "completed"
    assert result["termination_reason"] == "model_final"
    assert not any(x.get("type") == "clarification_requested" for x in result["trace"])
