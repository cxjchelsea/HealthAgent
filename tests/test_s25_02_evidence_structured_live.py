"""M2 structure: frozen semantic route, synthetic facts, strict references."""
from agent.route_semantic_hierarchical import HierarchicalDiagnostic
from agent.route_semantic_evidence import verify_core_evidence
from evals.s25_02_m2_hierarchical_boundary_cases import CASES
from evals.s25_02_m2_evidence_structured_live import (
    facts_for, proposal_from_core, extract_unique_ordinal,
    expected_disposition,
)

def find(name):
    return next(c for c in CASES if c.case_id == name)


def test_core_owns_route_without_second_route_classifier():
    c = find("resume_missing")
    p = proposal_from_core(HierarchicalDiagnostic("plan", "resume"),
                           c.prompt, c.prompt, None)
    assert p.primary_route == "plan_resume"
    r = verify_core_evidence(HierarchicalDiagnostic("plan", "resume"),
                             p, c.prompt, facts_for(c, "boundary"))
    assert (r.disposition, r.reason) == ("deny", "missing_saved_plan")
    assert r.host_authorization is False


def test_positive_fixtures_are_separate_from_negative():
    c = find("start_first")
    assert not facts_for(c, "boundary").revision_valid
    assert facts_for(c, "positive").revision_valid
    assert not facts_for(c, "boundary").tool_registered


def test_valid_revision_ordinal_and_checkpoint():
    c = find("modify_step")
    step = extract_unique_ordinal(c.prompt)
    assert step == "第二步"
    core = HierarchicalDiagnostic("plan_step", "revise")
    p = proposal_from_core(core, c.prompt, c.prompt, step)
    r = verify_core_evidence(core, p, c.prompt, facts_for(c, "positive"))
    assert r.disposition == "potentially_admissible"
    assert r.host_authorization is False


def test_quote_absence_and_duplicate_ordinal_are_not_repaired():
    assert extract_unique_ordinal("第二步和第二步都修改") is None
    c = find("start_first")
    core = HierarchicalDiagnostic("plan", "execute_first")
    p = proposal_from_core(core, c.prompt, None, None)
    r = verify_core_evidence(core, p, c.prompt, facts_for(c, "positive"))
    assert r.disposition == "needs_evidence"


def test_pending_positive_and_tool_negative():
    pcase = find("pending_supply")
    pcore = HierarchicalDiagnostic("pending_slot", "invoke")
    pending_facts = facts_for(pcase, "positive")
    p = proposal_from_core(pcore, pcase.prompt, pcase.prompt, None, pending_facts)
    assert p.effect_intent == "read_only"
    r = verify_core_evidence(pcore, p, pcase.prompt, pending_facts)
    assert r.disposition == "potentially_admissible"
    assert r.host_authorization is False
    u = find("tool_unavailable")
    ucore = HierarchicalDiagnostic("tool", "invoke")
    p2 = proposal_from_core(ucore, u.prompt, u.prompt, None, facts_for(u, "positive"))
    assert p2.effect_intent == "none"
    r2 = verify_core_evidence(ucore, p2, u.prompt, facts_for(u, "positive"))
    assert r2.disposition == "needs_evidence"
    assert r2.reason == "tool_effect_unspecified"
    assert r2.host_authorization is False


def test_pending_task_title_keeps_mutation_and_unknown_tool_does_not():
    from agent.route_shadow_host import ShadowHostFacts
    core = HierarchicalDiagnostic("pending_slot", "invoke")
    message = "审计共享账户"
    write_facts = ShadowHostFacts(
        has_plan=False, pending_action="create_task", pending_field="title",
        pending_original_request_bound=True, current_write_authorized=True,
        source_provenance_valid=True, tool_registered=True, tool_schema_valid=True)
    created = proposal_from_core(core, message, message, None, write_facts)
    assert created.effect_intent == "mutation_requested"
    admitted = verify_core_evidence(core, created, message, write_facts)
    assert admitted.disposition == "potentially_admissible"
    assert admitted.host_authorization is False
    unknown_facts = ShadowHostFacts(
        has_plan=False, pending_action="shell", pending_field="command",
        pending_original_request_bound=True, source_provenance_valid=True,
        tool_registered=True, tool_schema_valid=True)
    unknown = proposal_from_core(core, message, message, None, unknown_facts)
    assert unknown.effect_intent == "none"
    blocked = verify_core_evidence(core, unknown, message, unknown_facts)
    assert blocked.disposition == "needs_evidence"
    assert blocked.reason == "pending_tool_effect_unknown"
