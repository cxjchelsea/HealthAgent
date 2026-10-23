"""C01/C02/C03 arithmetic failure recovery must be goal-equivalent and evidenced."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop
from agent.arithmetic_recovery import arithmetic_equivalent


class Steps:
    def __init__(self, expressions):
        self.expressions = list(expressions)
    async def decide(self, messages, tools):
        if self.expressions:
            expression = self.expressions.pop(0)
            return Decision("", [{"id": str(len(messages)), "name": "calculate",
                                  "arguments": {"expression": expression}}])
        return Decision("done", [])


def test_c02_equivalent_recovery_marks_complete():
    result = asyncio.run(AgentLoop(Steps(["2**10", "2*2*2*2*2*2*2*2*2*2"])).run(
        "计算 2**10 如果失败换等价方式", include_evidence=True))
    assert result["status"] == "completed"
    assert result["termination_reason"] == "model_final"
    assert any(x["type"] == "arithmetic_failure_recovered" for x in result["trace"])
    assert [a["observation"]["ok"] for a in result["audit"]] == [False, True]


def test_c03_success_does_not_clear_unrelated_failure():
    result = asyncio.run(AgentLoop(Steps(["8/0", "2+2"])).run(
        "分别计算 8/0 与 2+2", include_evidence=True))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"
    assert not any(x["type"] == "arithmetic_failure_recovered" for x in result["trace"])


def test_c02_unrelated_success_does_not_clear_unsupported_expression():
    result = asyncio.run(AgentLoop(Steps(["2**10", "2+2"])).run(
        "分别计算 2**10 和 2+2", include_evidence=True))
    assert result["status"] == "partial"
    assert result["termination_reason"] == "unresolved_tool_failure"


def test_equivalence_safely_rejects_tricky_inputs():
    assert arithmetic_equivalent("2**10", "1024", 1024)
    assert not arithmetic_equivalent("1/0", "1", 1)
    assert not arithmetic_equivalent("2**1000000", "1024", 1024)
    assert not arithmetic_equivalent("__import__('os').system('echo x')", "0", 0)
    assert not arithmetic_equivalent("2**10", "2+2", 4)
