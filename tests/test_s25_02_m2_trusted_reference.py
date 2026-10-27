"""M2 references are bounded by trustworthy Host facts, never self-authorizing."""
import pytest
from agent.route_shadow import ShadowRoute, verify_trusted_reference
from agent.route_shadow_host import ShadowHostFacts


def make(route, target, ref):
    return ShadowRoute.model_validate({
        "schema_version":"s25-route-v2", "primary_route":route,
        "route_target":target, "execution_scope":(
            "single_tool" if route=="agent_loop" else "none"),
        "effect_intent":("read_only" if route=="agent_loop"
                         else "mutation_requested"),
        "ambiguity":"clear", "reference":ref})


def test_pending_trusted_field_conflict_fail_closed():
    p=make("agent_loop","pending_clarification",
           {"kind":"pending_slot","pending_field":"title"})
    with pytest.raises(ValueError,match="shadow_pending_field_conflict"):
        verify_trusted_reference(p,pending_field="term")


def test_pending_missing_reference_never_fabricated():
    p=make("agent_loop","pending_clarification",{"kind":"none"})
    with pytest.raises(ValueError,match="shadow_pending_reference_unverified"):
        verify_trusted_reference(p,pending_field="term")
    assert p.reference.kind=="none"


def test_pending_abstention_does_not_invent_field():
    p=make("agent_loop","pending_clarification",{"kind":"pending_slot"})
    verify_trusted_reference(p,pending_field="term")
    assert p.reference.pending_field is None


def test_revision_outside_trusted_plan_rejected():
    p=make("plan_revise","saved_plan",{"kind":"saved_step","step_number":3})
    with pytest.raises(ValueError,match="shadow_revision_step_out_of_range"):
        verify_trusted_reference(p,host_facts=ShadowHostFacts(
            has_plan=True,plan_states=("pending","pending")))


def test_revision_reference_not_inferred_from_existing_plan():
    p=make("plan_revise","saved_plan",{"kind":"none"})
    with pytest.raises(ValueError,match="shadow_revision_step_unverified"):
        verify_trusted_reference(p,host_facts=ShadowHostFacts(
            has_plan=True,plan_states=("pending",)))


def test_valid_saved_step_keeps_identity():
    p=make("plan_revise","saved_plan",{"kind":"saved_step","step_number":2})
    verify_trusted_reference(p,host_facts=ShadowHostFacts(
        has_plan=True,plan_states=("completed","pending")))
    assert p.reference.step_number == 2
