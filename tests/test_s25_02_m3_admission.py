"""M3 contract is deterministic and cannot authorize execution on its own."""
from dataclasses import replace

from agent.route_admission_m3 import PreTurnFacts, decide_admission
from agent.route_shadow import ShadowRoute


def route(kind="agent_loop", **kwargs):
    fields = dict(schema_version="s25-route-v2", primary_route=kind,
                  route_target="tool_action", execution_scope="single_tool",
                  effect_intent="read_only", ambiguity="clear")
    fields.update(kwargs)
    return ShadowRoute.model_validate(fields)


def facts(**changes):
    baseline = PreTurnFacts(conversation_id="c1", request_id="r1",
                            request_is_fresh=True, session_lock_held=True,
                            idempotency_authority_verified=True,
                            user_source_verified=True, tool_registered=True,
                            tool_arguments_valid=True)
    return replace(baseline, **changes)


def test_missing_provenance_fails_closed():
    d = decide_admission(route(), facts(user_source_verified=False))
    assert d.disposition == "deny"
    assert d.execution_authorized is False


def test_registered_tool_does_not_replace_schema_proof():
    d = decide_admission(route(), facts(tool_arguments_valid=False))
    assert d.reason == "tool_registry_or_schema_unverified"


def test_read_only_candidate_is_never_authorization():
    d = decide_admission(route(), facts())
    assert d.disposition == "candidate_execute"
    assert d.execution_authorized is False


def test_unregistered_tool_and_write_without_provenance_denied():
    assert decide_admission(route(), facts(tool_registered=False)).disposition == "deny"
    mutation = route(effect_intent="mutation_requested")
    assert decide_admission(mutation, facts(effect_authorized=False)).disposition == "deny"


def test_replay_and_missing_lock_deny():
    assert decide_admission(route(), facts(request_is_fresh=False)).disposition == "deny"
    assert decide_admission(route(), facts(session_lock_held=False)).disposition == "deny"


def test_pending_requires_original_binding_and_exact_field():
    pending = route(route_target="pending_clarification",
                    reference={"kind":"pending_slot","pending_field":"term"})
    assert decide_admission(pending, facts(
        pending_action="mcp_reference_lookup",pending_field="term",
        pending_original_request_verified=False)).disposition == "deny"
    assert decide_admission(pending, facts(
        pending_action="mcp_reference_lookup",pending_field="topic",
        pending_original_request_verified=True)).reason == "pending_field_conflict"


def test_cancel_is_not_unconditionally_noop():
    cancel = route("cancel", route_target="pending_clarification",
                   execution_scope="none", effect_intent="none")
    assert decide_admission(cancel, facts()).disposition == "deny"
    d = decide_admission(cancel, facts(
        pending_action="mcp_reference_lookup",pending_original_request_verified=True))
    assert d.disposition == "candidate_execute"
    assert d.execution_authorized is False


def test_saved_plan_binding_and_revision_required():
    resume = route("plan_resume",route_target="saved_plan",
                   execution_scope="remaining_steps")
    assert decide_admission(resume,facts(has_saved_plan=True)).disposition == "deny"
    d = decide_admission(resume, facts(
        has_saved_plan=True,checkpoint_revision=2,plan_schema_valid=True,
        checkpoint_binding_verified=True,plan_step_states=("completed","pending")))
    assert d.disposition == "candidate_execute"
    assert d.execution_authorized is False


def test_m3_does_not_route_an_effectful_action_without_target():
    proposal = route(route_target="none")
    assert decide_admission(proposal,facts()).reason == "tool_target_missing"
