"""Unified plan semantic action preserves execution scope without Host authority."""
import asyncio
import json

from agent.route_semantic_hierarchical import classify_hierarchical_diagnostic
from agent.route_shadow_host import ShadowHostFacts


class Stub:
    def __init__(self, object_type, action):
        self.answers = iter([{"object_type": object_type}, {"action": action}])
        self.calls = []

    async def complete(self, **kw):
        self.calls.append(kw)
        return json.dumps(next(self.answers))


def classify(initial, action, message, facts):
    provider = Stub(initial, action)
    result = asyncio.run(classify_hierarchical_diagnostic(
        provider, "fixture", message, facts, 2))
    return result, provider


def test_all_plan_actions_from_single_semantic_decision():
    actions = [
        ("create_plan", "plan", "create"),
        ("execute_first", "plan", "execute_first"),
        ("resume_remaining", "plan", "resume"),
        ("edit_step_definition", "plan_step", "revise"),
        ("cancel_plan", "plan", "cancel"),
    ]
    for action, obj, op in actions:
        result, provider = classify("plan", action, "请处理这份计划",
                                    ShadowHostFacts(has_plan=False))
        assert (result.object_type, result.operation) == (obj, op)
        assert len(provider.calls) == 2
        payload = json.loads(provider.calls[1]["messages"][1]["content"])
        assert "saved_plan_exists" not in payload["trusted_host_state"]


def test_step_initial_choice_can_be_restored_to_plan_without_extra_decider():
    result, provider = classify("plan_step", "resume_remaining",
        "没有任何存档计划也继续未完成的工作", ShadowHostFacts(has_plan=False))
    assert (result.object_type, result.operation) == ("plan", "resume")
    assert len(provider.calls) == 2


def test_plan_initial_choice_can_be_corrected_to_step_edit():
    result, provider = classify("plan", "edit_step_definition",
        "改写已存步骤说明，但不运行", ShadowHostFacts(has_plan=True))
    assert (result.object_type, result.operation) == ("plan_step", "revise")
    assert len(provider.calls) == 2


def test_uncertain_model_result_fails_closed():
    result, provider = classify("plan_step", "uncertain",
        "处理那一步", ShadowHostFacts(has_plan=True))
    assert result.failure_stage == "plan_action"
    assert result.operation is None
    assert len(provider.calls) == 2
