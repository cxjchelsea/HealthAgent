"""S25-02 Final Step 6: shadow schema mismatch correction is bounded and inert."""
import asyncio
import json

import pytest

from agent.route_shadow import evaluate_shadow


class ScriptedProvider:
    def __init__(self, *answers):
        self.answers = answers
        self.calls = 0

    async def complete(self, **kwargs):
        answer = self.answers[self.calls]
        self.calls += 1
        return json.dumps(answer, ensure_ascii=False)


def proposal(route, *, target, scope, effect, clarification=None, reference=None):
    return {
        "schema_version": "s25-route-v2",
        "primary_route": route, "route_target": target,
        "execution_scope": scope, "effect_intent": effect,
        "ambiguity": "clear", "source_quote": "",
        "clarification": clarification or {"required": False, "reason": "none", "question": None},
        "reference": reference or {"kind": "none"},
    }


def revised(scope):
    return proposal("plan_revise", target="saved_plan",
        scope=scope, effect="mutation_requested",
        reference={"kind": "saved_step", "step_number": 2})


def clarified(effect):
    return proposal("clarify", target="none", scope="none",
        effect=effect, clarification={"required": True,
                                      "reason": "missing_argument",
                                      "question": "请提供查询关键词"})


@pytest.mark.parametrize("broken,fixed,message,has_plan", [
    (revised("single_tool"), revised("none"), "把第二步改成计算7*8", True),
    (clarified("read_only"), clarified("none"), "请查询", False),
])
def test_diagnostic_retry_accepts_only_schema_valid_correction(
        broken, fixed, message, has_plan):
    provider = ScriptedProvider(broken, fixed)
    result = asyncio.run(evaluate_shadow(
        provider, "model", message, has_plan=has_plan, pending_action=None))
    assert provider.calls == 2
    assert result["state"] == "valid"
    assert result["route"] == fixed["primary_route"]
    assert result["effect"] == fixed["effect_intent"]
    assert result["scope"] == fixed["execution_scope"]
    assert result["host_admission_complete"] is False


def test_retry_invalid_same_schema_stays_unavailable():
    provider = ScriptedProvider(revised("single_tool"), revised("single_tool"))
    result = asyncio.run(evaluate_shadow(
        provider, "model", "把第二步改成计算7*8",
        has_plan=True, pending_action=None))
    assert provider.calls == 2
    assert result["state"] == "unavailable"
    assert result["error_class"] == "ValidationError"
    assert result["combination_diagnostic"]["invalid_fields"] == ["execution_scope"]


def test_retry_must_not_switch_primary_route():
    provider = ScriptedProvider(
        clarified("read_only"),
        proposal("respond", target="none", scope="none", effect="none"))
    result = asyncio.run(evaluate_shadow(
        provider, "model", "请查询", has_plan=False, pending_action=None))
    assert provider.calls == 2
    assert result["state"] == "unavailable"
    assert result["error_class"] == "ValueError"
    assert result["validation_code"] == "other_value_error"


def test_unrelated_invalid_schema_not_retried():
    provider = ScriptedProvider(
        proposal("respond", target="tool_action", scope="single_tool",
                 effect="read_only"),
        proposal("respond", target="none", scope="none", effect="none"))
    # Other route-combination failures are not eligible for this narrowly
    # scoped Shadow-only retry and still fail closed on their first attempt.
    result = asyncio.run(evaluate_shadow(
        provider, "model", "你好", has_plan=False, pending_action=None))
    assert provider.calls == 1
    assert result["state"] == "unavailable"
    assert result["error_class"] == "ValidationError"


def test_invalid_reference_never_retried_or_repaired():
    bad = revised("none")
    bad["reference"] = {"kind": "saved_step", "step_number": 0}
    provider = ScriptedProvider(bad)
    result = asyncio.run(evaluate_shadow(
        provider, "model", "把第二步改成计算7*8", has_plan=True,
        pending_action=None))
    assert provider.calls == 1
    assert result["state"] == "unavailable"


def test_revision_target_excludes_explicit_unchanged_step():
    from agent.route_shadow import extract_revision_target_step, bind_explicit_revision_step
    from agent.route_shadow_host import ShadowHostFacts
    message = "将已保存计划的第二步由计算 5*6 改为计算 7*8，第一步不变，不要执行。"
    assert extract_revision_target_step(message) == ("第二步", 2)
    proposal = revised("none")
    proposal["reference"] = {"kind": "none"}
    from agent.route_shadow import ShadowRoute
    route = ShadowRoute.model_validate(proposal)
    bound = bind_explicit_revision_step(
        route, message, ShadowHostFacts(
            has_plan=True, plan_states=("pending", "pending"),
            revision_valid=True))
    assert bound.reference.kind == "saved_step"
    assert bound.reference.step_number == 2


@pytest.mark.parametrize("message", [
    "第二步改为计算7*8，第一步也改为计算9*9",
    "第二步和第一步都修改一下",
    "将第二步改为计算7*8，并把第二步再改成计算9*9",
    "第一步不变，第二步也不变",
])
def test_revision_target_abstains_on_multiple_or_non_changes(message):
    from agent.route_shadow import extract_revision_target_step
    assert extract_revision_target_step(message) is None


def test_revision_target_preserves_unambiguous_single_reference():
    from agent.route_shadow import extract_revision_target_step
    assert extract_revision_target_step("请修改第二步，不要执行") == ("第二步", 2)


def test_revision_target_does_not_promote_missing_checkpoint():
    from agent.route_shadow import ShadowRoute, bind_explicit_revision_step
    from agent.route_shadow_host import ShadowHostFacts
    request = revised("none")
    request["reference"] = {"kind": "none"}
    route = ShadowRoute.model_validate(request)
    msg = "将第二步由计算5*6改为计算7*8，第一步不变"
    result = bind_explicit_revision_step(
        route, msg, ShadowHostFacts(
            has_plan=False, plan_states=("pending", "pending"),
            revision_valid=True))
    assert result.reference.kind == "none"
