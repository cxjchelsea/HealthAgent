"""M2 mechanism regression: literal cross-language binding and safe cancellation source."""
from dataclasses import replace

from agent.route_shadow import extract_unique_step_ordinal, bind_explicit_revision_step
from agent.route_semantic_hierarchical import HierarchicalDiagnostic, OBJECT_PROMPT
from evals.s25_02_m2_evidence_structured_live import proposal_from_core, extract_unique_ordinal
from agent.route_shadow_host import ShadowHostFacts
from agent.route_semantic_evidence import verify_core_evidence


def facts(**overrides):
    base = ShadowHostFacts(has_plan=True, plan_states=("pending", "pending", "pending"),
                           revision_valid=True, bound_step_valid=False)
    return replace(base, **overrides)


def test_chinese_english_ordinals_and_ambiguity():
    assert extract_unique_step_ordinal("将第三步修改") == ("第三步", 3)
    assert extract_unique_step_ordinal("Please update step 2.") == ("step 2", 2)
    assert extract_unique_step_ordinal("Update STEP 1 now") == ("STEP 1", 1)
    assert extract_unique_step_ordinal("Update step 1 then step 2") is None
    assert extract_unique_step_ordinal("Update the step") is None
    assert extract_unique_step_ordinal("step 11") is None
    assert extract_unique_ordinal("please revise step 3") == "step 3"


def test_ordinal_binding_never_fabricates_out_of_range_or_missing_checkpoint():
    core = HierarchicalDiagnostic("plan_step", "revise")
    msg = "Please revise step 3 of the saved workflow."
    route = proposal_from_core(core, msg, msg, "step 3", facts())
    assert bind_explicit_revision_step(route, msg, facts()).reference.step_number == 3
    assert bind_explicit_revision_step(route, msg, facts(plan_states=("pending",))).reference.kind == "none"
    assert bind_explicit_revision_step(route, msg, facts(revision_valid=False)).reference.kind == "none"
    assert bind_explicit_revision_step(route, "step 2 and step 3", facts()).reference.kind == "none"


def test_source_quote_and_checkpoint_do_not_grant_revision_authority():
    core = HierarchicalDiagnostic("plan_step", "revise")
    msg = "Please revise step 2."
    route = proposal_from_core(core, msg, msg, "step 2", facts())
    result = verify_core_evidence(core, route, msg, facts())
    assert result.host_authorization is False
    assert result.disposition == "needs_evidence"


def test_pending_cancel_source_is_only_diagnostic():
    core = HierarchicalDiagnostic("pending_slot", "cancel")
    message = "Cancel the pending lookup."
    bound = facts(pending_action="mcp_reference_lookup", pending_field="query",
                  pending_original_request_bound=True)
    route = proposal_from_core(core, message, message, None, bound)
    result = verify_core_evidence(core, route, message, bound)
    assert result.host_authorization is False
    assert result.candidate_route == "cancel"
    assert result.disposition == "safe_non_effect"
    missing = proposal_from_core(core, message, None, None, bound)
    denied = verify_core_evidence(core, missing, message, bound)
    assert denied.disposition == "needs_evidence"


def test_prompt_changes_are_general_and_do_not_embed_failures():
    assert "separately ordered actions" in OBJECT_PROMPT
    assert "uniquely bound missing" in OBJECT_PROMPT
    assert "plan_create_positive_01" not in OBJECT_PROMPT
