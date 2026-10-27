from dataclasses import replace
from agent.route_semantic_hierarchical import HierarchicalDiagnostic
from agent.route_semantic_evidence import verify_core_evidence
from agent.route_shadow import ShadowRoute, QuoteEvidence, ShadowReference
from agent.route_shadow_host import ShadowHostFacts

core = HierarchicalDiagnostic("plan_step", "revise")
msg = "请修改第二步，不要执行"

def facts():
    return ShadowHostFacts(has_plan=True, plan_states=("pending", "pending"),
        revision_valid=True, bound_step_valid=True, current_write_authorized=True,
        source_provenance_valid=True)

def route(quote=msg, ordinal="第二步"):
    return ShadowRoute(schema_version="s25-route-v2", primary_route="plan_revise",
        route_target="saved_plan", execution_scope="none", effect_intent="mutation_requested",
        ambiguity="clear", source_quote_evidence=[QuoteEvidence(exact_text=quote)],
        plan_step_quote_evidence=[QuoteEvidence(exact_text=ordinal)])

def test_missing_checkpoint_is_unverified_not_invalid():
    x = verify_core_evidence(core, route(), msg, replace(facts(), revision_valid=False))
    assert (x.disposition, x.reason) == ("needs_evidence", "checkpoint_revision_unverified")

def test_valid_checkpoint_remains_preview_only():
    x = verify_core_evidence(core, route(), msg, facts())
    assert x.disposition == "potentially_admissible"
    assert x.host_authorization is False

def test_forged_quote_denied():
    x = verify_core_evidence(core, route(quote="不存在"), msg, facts())
    assert (x.disposition, x.reason) == ("deny", "source_quote_not_found")

def test_out_of_range_step_denied():
    p = ShadowRoute(schema_version="s25-route-v2", primary_route="plan_revise",
        route_target="saved_plan", execution_scope="none", effect_intent="mutation_requested",
        ambiguity="clear", source_quote_evidence=[QuoteEvidence(exact_text="修改第三步")],
        reference=ShadowReference(kind="saved_step", step_number=3))
    x = verify_core_evidence(core, p, "修改第三步", facts())
    assert (x.disposition, x.reason) == ("deny", "revision_step_out_of_range")

def test_ambiguous_source_denied():
    x = verify_core_evidence(core, route(quote="第二步"), "第二步和第二步都修改", facts())
    assert (x.disposition, x.reason) == ("deny", "source_quote_ambiguous")
