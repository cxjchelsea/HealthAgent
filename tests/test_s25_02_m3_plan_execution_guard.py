"""Saved-plan execution must revalidate the Turn-bound snapshot before dispatch."""
import asyncio

from agent.loop import AgentLoop
from agent.planning import PlanLedger


def checkpoint(*steps):
    ledger = PlanLedger()
    assert ledger.propose([
        {"goal": f"calculate {item}", "expected_tool": "calculate",
         "expected_arguments": {"expression": item}}
        for item in steps
    ])["ok"]
    return ledger.checkpoint()


def test_stale_plan_guard_blocks_before_any_tool_dispatch():
    calls = []
    loop = AgentLoop(decider=object(), execution_guard=lambda: calls.append("guard") or False)
    result = asyncio.run(loop.run(
        "继续执行已保存计划", plan_checkpoint=checkpoint("19+23"),
        plan_control="execute_first"))

    assert calls == ["guard"]
    assert result["status"] == "partial"
    assert result["termination_reason"] == "plan_execution_guard_conflict"
    assert result["plan"][0]["status"] == "pending"
    assert not any(event.get("type") == "tool" for event in result["trace"])


def test_plan_guard_is_rechecked_before_each_saved_step():
    decisions = iter([True, False])
    calls = []

    def guard():
        value = next(decisions)
        calls.append(value)
        return value

    loop = AgentLoop(decider=object(), execution_guard=guard)
    result = asyncio.run(loop.run(
        "继续执行已保存计划",
        plan_checkpoint=checkpoint("19+23", "6*7"),
        plan_control="resume"))

    assert calls == [True, False]
    assert result["status"] == "partial"
    assert result["termination_reason"] == "plan_execution_guard_conflict"
    assert result["plan"][0]["status"] == "completed"
    assert result["plan"][1]["status"] == "pending"
    assert [event.get("name") for event in result["trace"]
            if event.get("type") == "tool"] == ["calculate"]
