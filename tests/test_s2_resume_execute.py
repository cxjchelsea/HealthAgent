"""Deterministic continuation of an existing tool-bound plan, not replanning."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop
from agent.planning import PlanLedger


class NoMoreDecisions:
    async def decide(self, messages, tools):
        raise AssertionError("Unchanged, bound resume must execute original tools")


class FakeMCP:
    def call(self, name, arguments):
        assert name == "mcp_reference_lookup"
        assert arguments == {"term": "rag"}
        return {"ok": True, "value": "retrieval-augmented generation"}


def original_checkpoint():
    ledger = PlanLedger()
    ledger.propose([
        {"goal": "计算 8×8", "expected_tool": "calculate",
         "expected_arguments": {"expression": "8*8"}},
        {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
         "expected_arguments": {"term": "rag"}},
        {"goal": "计算 9×9", "expected_tool": "calculate",
         "expected_arguments": {"expression": "9*9"}},
    ])
    ledger.observe("calculate", {"ok": True, "value": 64}, {"expression": "8*8"})
    return ledger.checkpoint()


def test_continue_pending_steps_without_replanning():
    out = asyncio.run(AgentLoop(NoMoreDecisions(), mcp_client=FakeMCP()).run(
        "请继续完成刚才计划中尚未完成的步骤，并给出最终结果。",
        plan_checkpoint=original_checkpoint(), plan_control="resume", include_evidence=True))
    assert out["status"] == "completed"
    assert out["plan_revision"] == 1
    assert "retrieval-augmented generation" in out["answer"]
    assert "81" in out["answer"]
    assert "此前已验证完成" in out["answer"]
    assert [s["id"] for s in out["plan"]] == [1, 2, 3]
    assert all(s["status"] == "completed" for s in out["plan"])
    assert [t["name"] for t in out["trace"] if t["type"] == "tool"] == [
        "mcp_reference_lookup", "calculate"]
    assert all(x["event"] != "plan_revised" for x in out["plan_events"])


class FailMCP:
    def call(self, name, arguments):
        return {"ok": False, "error": "mcp_unavailable"}


def test_failed_pending_tool_keeps_checkpoint_for_retry():
    out = asyncio.run(AgentLoop(NoMoreDecisions(), mcp_client=FailMCP()).run(
        "请继续完成原计划尚未完成的步骤。",
        plan_checkpoint=original_checkpoint(), plan_control="resume"))
    assert out["status"] == "partial"
    assert [x["status"] for x in out["plan"]] == ["completed", "pending", "pending"]
    assert out["plan_revision"] == 1
    assert "步骤 2：尚未完成" in out["answer"]
    assert "81" not in out["answer"]
    assert "tool_failed" in [x["event"] for x in out["plan_events"]]


class ChangedGoalDecision:
    async def decide(self, messages, tools):
        return Decision("先讨论新目标。", [])


def test_changed_goal_does_not_force_old_pending_execution():
    out = asyncio.run(AgentLoop(ChangedGoalDecision(), mcp_client=FakeMCP()).run(
        "继续完成剩余步骤，但是更正目标：先不要查询 RAG。",
        plan_checkpoint=original_checkpoint()))
    assert not any(t["type"] == "plan_resume_execution" for t in out["trace"])
    assert [s["status"] for s in out["plan"]] == ["completed", "pending", "pending"]
