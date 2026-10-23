"""M2 shadow matrix: no hypothetical route can invent Host authority."""
import pytest
from agent.route_shadow import ShadowRoute
from agent.route_shadow_host import ShadowHostFacts, preview_admission


def proposal(route, target="saved_plan", scope="none", effect="mutation_requested",
             **kwargs):
    return ShadowRoute.model_validate({
        "schema_version":"s25-route-v2", "primary_route":route,
        "route_target":target, "execution_scope":scope,
        "effect_intent":effect, "ambiguity":"clear", **kwargs})


@pytest.mark.parametrize("route,target,scope,effect", [
    ("respond","none","none","none"),
    ("cancel","none","none","none"),
    ("clarify","none","none","none"),
])
def test_non_effect_routes_never_authorize_tools(route,target,scope,effect):
    extra = {"clarification":{"required":True,"reason":"missing_argument",
                               "question":"哪个参数？"}} if route=="clarify" else {}
    result = preview_admission(proposal(route,target,scope,effect,**extra),
                               ShadowHostFacts(has_plan=False))
    assert result.disposition == "safe_non_effect"


@pytest.mark.parametrize("route,scope", [
    ("plan_execute_first","first_step"),
    ("plan_resume","remaining_steps"),
    ("plan_revise","none"),
])
def test_missing_plan_denied(route,scope):
    result = preview_admission(proposal(route,scope=scope,
                    effect="read_only" if route!="plan_revise" else "mutation_requested"),
                    ShadowHostFacts(has_plan=False))
    assert result.disposition == "deny"


def test_first_step_never_becomes_second_step():
    result = preview_admission(proposal(
        "plan_execute_first",scope="first_step",effect="read_only"),
        ShadowHostFacts(has_plan=True,plan_states=("completed","pending"),
                        revision_valid=True,bound_step_valid=True,current_read_bound=True))
    assert result.reason == "first_step_already_completed"
    assert result.disposition == "safe_non_effect"


def test_completed_plan_resume_cannot_repeat_tools():
    result = preview_admission(proposal(
        "plan_resume",scope="remaining_steps",effect="read_only"),
        ShadowHostFacts(has_plan=True,plan_states=("completed","completed"),
                        revision_valid=True,bound_step_valid=True,current_read_bound=True))
    assert result.reason == "plan_already_completed"


def test_saved_plan_not_implicitly_resumed_by_standalone_tool():
    result = preview_admission(proposal(
        "agent_loop",target="tool_action",scope="single_tool",effect="read_only"),
        ShadowHostFacts(has_plan=True,plan_states=("pending","pending")))
    assert result.disposition == "needs_evidence"
    assert result.route == "agent_loop"


def test_write_requires_explicit_host_evidence():
    request = proposal("agent_loop",target="tool_action",
                       scope="single_tool",effect="mutation_requested")
    result = preview_admission(request, ShadowHostFacts(
        has_plan=False,tool_registered=True,current_write_authorized=False))
    assert result.disposition == "needs_evidence"
    assert result.reason == "write_source_unverified"


def test_pending_field_mismatch_is_denied():
    request = proposal(
        "agent_loop",target="pending_clarification",scope="single_tool",
        effect="read_only",reference={"kind":"pending_slot","pending_field":"title"})
    result = preview_admission(request, ShadowHostFacts(
        has_plan=False,pending_action="mcp_reference_lookup",pending_field="term"))
    assert result.disposition == "deny"
    assert result.reason == "pending_field_mismatch"


def test_plan_revision_requires_step_binding_and_source():
    request = proposal("plan_revise",scope="none")
    result = preview_admission(request, ShadowHostFacts(
        has_plan=True,plan_states=("pending","pending"),revision_valid=True))
    assert result.disposition == "needs_evidence"
    assert result.reason == "revision_binding_unverified"


def test_plan_create_cannot_overwrite_saved_plan():
    result = preview_admission(proposal("plan_create"),
                               ShadowHostFacts(has_plan=True))
    assert result.disposition == "deny"


def test_all_evidence_still_only_potential_not_authorized():
    result = preview_admission(proposal(
        "plan_resume",scope="remaining_steps",effect="read_only"),
        ShadowHostFacts(has_plan=True,plan_states=("completed","pending"),
                        revision_valid=True,bound_step_valid=True,current_read_bound=True))
    assert result.disposition == "potentially_admissible"
    assert result.reason == "requires_tool_permission_verification"
