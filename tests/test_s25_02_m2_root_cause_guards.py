"""M2 effect/plan proposals cannot be admitted without adequate typed proof."""
from agent.route_shadow import ShadowRoute
from agent.route_shadow_host import ShadowHostFacts, preview_admission


def make(kind, target, scope, effect, **extra):
    return ShadowRoute.model_validate({
        "schema_version": "s25-route-v2", "primary_route": kind,
        "route_target": target, "execution_scope": scope,
        "effect_intent": effect, "ambiguity": "clear", **extra})


def trusted_pending():
    return ShadowHostFacts(
        has_plan=False, pending_action="mcp_reference_lookup", pending_field="term",
        pending_original_request_bound=True, source_provenance_valid=True,
        tool_registered=True, tool_schema_valid=True)


def test_pending_effect_unspecified_is_never_potentially_admissible():
    p = make("agent_loop", "pending_clarification", "single_tool", "none",
             reference={"kind": "pending_slot", "pending_field": "term"})
    result = preview_admission(p, trusted_pending())
    assert result.disposition == "needs_evidence"
    assert result.reason == "pending_effect_unspecified"


def test_tool_effect_unspecified_is_not_text_only():
    p = make("agent_loop", "tool_action", "single_tool", "none")
    result = preview_admission(p, ShadowHostFacts(has_plan=False))
    assert result.disposition == "needs_evidence"
    assert result.reason == "tool_effect_unspecified"


def test_single_task_mislabeled_as_plan_lacks_multistep_evidence():
    p = make("plan_create", "none", "none", "mutation_requested")
    result = preview_admission(p, ShadowHostFacts(
        has_plan=False, plan_schema_valid=True, source_provenance_valid=True,
        current_write_authorized=True))
    assert result.disposition == "needs_evidence"
    assert result.reason == "plan_steps_source_unverified"


def test_plan_revision_requires_typed_step_reference():
    p = make("plan_revise", "saved_plan", "none", "mutation_requested")
    result = preview_admission(p, ShadowHostFacts(
        has_plan=True, revision_valid=True, bound_step_valid=True,
        current_write_authorized=True, source_provenance_valid=True))
    assert result.disposition == "needs_evidence"
    assert result.reason == "revision_step_reference_unverified"
