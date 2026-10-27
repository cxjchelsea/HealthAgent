"""Only original-request-bound Pending state may bind a Shadow reference."""
from agent.route_shadow import ShadowRoute, bind_diagnostic_pending_reference
from agent.route_shadow_host import ShadowHostFacts


def proposal(effect="read_only", reference=None):
    return ShadowRoute.model_validate({
        "schema_version":"s25-route-v2",
        "primary_route":"agent_loop",
        "route_target":"pending_clarification",
        "execution_scope":"single_tool",
        "effect_intent":effect,
        "ambiguity":"clear",
        "reference":reference or {"kind":"none"},
    })


def trusted(**changes):
    data={"has_plan":False,"pending_action":"mcp_reference_lookup",
          "pending_field":"term","pending_original_request_bound":True}
    data.update(changes)
    return ShadowHostFacts(**data)


def bind(p, facts):
    return bind_diagnostic_pending_reference(
        p,pending_action="mcp_reference_lookup",
        pending_field="term",host_facts=facts)


def test_trusted_single_pending_ref_is_bounded_shadow_copy():
    original=proposal()
    bound=bind(original,trusted())
    assert original.reference.kind=="none"
    assert bound.reference.kind=="pending_slot"
    assert bound.reference.pending_field=="term"
    assert bound.effect_intent=="read_only"
    assert bound.route_target=="pending_clarification"


def test_unbound_pending_origin_never_upgrades():
    assert bind(proposal(),trusted(pending_original_request_bound=False)).reference.kind=="none"


def test_mismatched_tool_or_field_fails_closed():
    assert bind(proposal(),trusted(pending_field="title")).reference.kind=="none"
    assert bind(proposal(),trusted(pending_action="create_task")).reference.kind=="none"


def test_effect_none_cannot_use_pending_binding():
    assert bind(proposal(effect="none"),trusted()).reference.kind=="none"


def test_explicit_reference_is_never_rewritten():
    original=proposal(reference={"kind":"pending_slot","pending_field":"other"})
    assert bind(original,trusted()).reference.pending_field=="other"
