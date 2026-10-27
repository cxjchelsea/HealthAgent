"""Ensure intent classification cannot read saved-plan existence/progress."""
import asyncio
import json

from agent.route_semantic_hierarchical import classify_hierarchical_diagnostic
from agent.route_shadow_host import ShadowHostFacts


class Observer:
    def __init__(self, object_type, operation):
        self.answers = iter([
            {"object_type": object_type},
            {"operation": operation},
        ])
        self.payloads = []

    async def complete(self, **kwargs):
        self.payloads.append(json.loads(kwargs["messages"][-1]["content"]))
        return json.dumps(next(self.answers))


def test_missing_plan_does_not_mask_requested_resume_target():
    provider = Observer("plan", "resume")
    result = asyncio.run(classify_hierarchical_diagnostic(
        provider, "fixture",
        "没有任何计划记录也请继续执行余下操作。",
        ShadowHostFacts(has_plan=False, plan_states=()), 2))
    assert (result.object_type, result.operation) == ("plan", "resume")
    assert "saved_plan_exists" not in provider.payloads[0]["trusted_host_state"]
    assert "saved_plan_step_count" not in provider.payloads[0]["trusted_host_state"]
    # Later stages retain actual Host state, for downstream guards.
    assert provider.payloads[1]["trusted_host_state"]["saved_plan_exists"] is False


def test_saved_plan_availability_does_not_change_object_stage_contract():
    snapshots = []
    for has_plan, steps in [(False, ()), (True, ("pending", "pending"))]:
        provider = Observer("plan", "resume")
        asyncio.run(classify_hierarchical_diagnostic(
            provider, "fixture", "继续执行计划剩余步骤",
            ShadowHostFacts(has_plan=has_plan, plan_states=steps), 2))
        snapshots.append(provider.payloads[0])
    assert snapshots[0] == snapshots[1]


def test_bound_pending_reference_is_still_available_to_semantic_stage():
    facts = ShadowHostFacts(has_plan=False, pending_action="calculate",
        pending_field="expression", pending_original_request_bound=True)
    provider = Observer("pending_slot", "invoke")
    result = asyncio.run(classify_hierarchical_diagnostic(
        provider, "fixture", "表达式为 6*7", facts, 2))
    assert result.object_type == "pending_slot"
    first = provider.payloads[0]["trusted_host_state"]
    assert first["pending_tool_request_bound"] is True
    assert first["pending_missing_field"] == "expression"
