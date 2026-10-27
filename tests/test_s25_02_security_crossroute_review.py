"""Cross-path security regressions for S25-02 review closure."""
import asyncio
from agent.capability_review import CapabilityReview, host_unsupported_capability
from agent.loop import AgentLoop
from agent.plan_intent import PlanRequest, build_plan_checkpoint
from agent.decision import Decision


def test_unavailable_explicit_shell_cannot_be_relabelled_as_calculator():
    proposal = CapabilityReview(explicitly_requested_tool="shell",
                                request_is_clear=True, resolved_tool_id="calculate")
    assert host_unsupported_capability(
        proposal, "请用 shell 执行命令", {"calculate", "mcp_reference_lookup"}) == "shell"


def test_chinese_calculator_reference_maps_to_registered_tool():
    proposal = CapabilityReview(explicitly_requested_tool="计算工具",
                                request_is_clear=True, resolved_tool_id="calculate")
    assert host_unsupported_capability(
        proposal, "请使用计算工具", {"calculate"}) is None


def test_untrusted_keyword_cannot_run_saved_plan_without_control():
    cp = build_plan_checkpoint("计算 8*8", PlanRequest(
        intent="plan_only", steps=[{"goal":"计算 8*8",
        "expected_tool":"calculate", "expected_arguments":{"expression":"8*8"}}]))
    class Decider:
        async def decide(self, messages, tools):
            return Decision("没有获得正式执行指令。", [])
    out = asyncio.run(AgentLoop(Decider()).run(
        "继续执行计划中剩余步骤", plan_checkpoint=cp))
    assert not any(event.get("type") == "plan_resume_execution"
                   for event in out["trace"])
    assert not any(event.get("type") == "tool" for event in out["trace"])
    assert out["plan"][0]["status"] == "pending"
