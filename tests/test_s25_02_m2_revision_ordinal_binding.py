"""M2 ordinal binding is evidence, not a routing or authorization shortcut."""
import asyncio
import json

from agent.route_shadow import (
    ShadowRoute, bind_explicit_revision_step, evaluate_shadow,
)
from agent.route_shadow_host import ShadowHostFacts


def revision(reference=None):
    return ShadowRoute.model_validate({
        "schema_version": "s25-route-v2", "primary_route": "plan_revise",
        "route_target": "saved_plan", "execution_scope": "none",
        "effect_intent": "mutation_requested", "ambiguity": "clear",
        "reference": reference or {"kind": "none"},
    })


def facts(**extras):
    data = {"has_plan": True, "revision_valid": True,
            "plan_states": ("pending", "pending")}
    data.update(extras)
    return ShadowHostFacts(**data)


def test_unique_explicit_step_binds_without_changing_semantics():
    original = revision()
    bound = bind_explicit_revision_step(
        original, "把已保存计划第二步改成查资料，不执行", facts())
    assert bound.reference.kind == "saved_step"
    assert bound.reference.step_number == 2
    assert original.reference.kind == "none"
    assert bound.effect_intent == "mutation_requested"


def test_out_of_range_or_missing_checkpoint_stays_unbound():
    assert bind_explicit_revision_step(revision(), "修改第八步", facts()).reference.kind == "none"
    assert bind_explicit_revision_step(revision(), "修改第二步",
            facts(revision_valid=False)).reference.kind == "none"


def test_multiple_step_mentions_do_not_choose_arbitrarily():
    assert bind_explicit_revision_step(
        revision(), "把第一步和第二步交换", facts()).reference.kind == "none"


def test_existing_explicit_reference_never_rewritten():
    p = revision({"kind": "saved_step", "step_number": 1})
    assert bind_explicit_revision_step(p, "修改第二步", facts()).reference.step_number == 1


def test_schema_combination_reports_only_bounded_invalid_fields():
    data = {
        "schema_version": "s25-route-v2", "primary_route": "plan_resume",
        "route_target": "none", "execution_scope": "first_step",
        "effect_intent": "none", "ambiguity": "clear",
        "source_quote": "SENSITIVE_TEXT",
    }
    class Stub:
        async def complete(self, **kwargs):
            return json.dumps(data)
    result = asyncio.run(evaluate_shadow(
        Stub(), "stub", "继续之前的计划", has_plan=False, pending_action=None))
    assert result["state"] == "unavailable"
    assert result["combination_diagnostic"]["invalid_fields"] == [
        "route_target", "execution_scope", "effect_intent"]
    assert "SENSITIVE_TEXT" not in json.dumps(result)
