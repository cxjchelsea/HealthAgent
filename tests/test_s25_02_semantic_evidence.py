"""Core-to-evidence read-only admission tests, including adversarial cases."""
from agent.route_semantic_hierarchical import HierarchicalDiagnostic
from agent.route_semantic_evidence import verify_core_evidence
from agent.route_shadow import ShadowRoute, ShadowReference, QuoteEvidence
from agent.route_shadow_host import ShadowHostFacts


def core(obj, op):
    return HierarchicalDiagnostic(obj, op)


def proposal(route, target, scope, effect, message, **extra):
    return ShadowRoute(
        schema_version="s25-route-v2", primary_route=route,
        route_target=target, execution_scope=scope, effect_intent=effect,
        ambiguity="clear", source_quote_evidence=[QuoteEvidence(exact_text=message)],
        **extra,
    )


def test_saved_first_step_preview_is_not_execution_authority():
    message = "请先执行第一步"
    p = proposal("plan_execute_first", "saved_plan", "first_step",
                 "read_only", message)
    f = ShadowHostFacts(
        has_plan=True, plan_states=("pending", "pending"), revision_valid=True,
        bound_step_valid=True, current_read_bound=True,
        source_provenance_valid=True, tool_registered=True, tool_schema_valid=True)
    result = verify_core_evidence(core("plan", "execute_first"), p, message, f)
    assert result.disposition == "potentially_admissible"
    assert result.host_authorization is False


def test_missing_plan_denies_first_step():
    msg = "请先执行第一步"
    p = proposal("plan_execute_first", "saved_plan", "first_step", "read_only", msg)
    r = verify_core_evidence(core("plan", "execute_first"), p, msg,
                             ShadowHostFacts(has_plan=False))
    assert (r.disposition, r.reason) == ("deny", "missing_saved_plan")


def test_step_revision_only_binds_explicit_valid_checkpoint_ordinal():
    msg = "修改第二步"
    p = proposal("plan_revise", "saved_plan", "none", "mutation_requested", msg,
                 plan_step_quote_evidence=[QuoteEvidence(exact_text="第二步")])
    facts = ShadowHostFacts(
        has_plan=True, plan_states=("pending", "pending"), revision_valid=True,
        bound_step_valid=True, current_write_authorized=True,
        source_provenance_valid=True)
    result = verify_core_evidence(core("plan_step", "revise"), p, msg, facts)
    assert result.disposition == "potentially_admissible"
    assert result.host_authorization is False
    bad = verify_core_evidence(core("plan_step", "revise"), p, msg,
                               ShadowHostFacts(has_plan=True, plan_states=("pending",),
                                               revision_valid=True))
    assert bad.disposition == "deny"


def test_pending_binding_requires_trusted_original_and_rejects_conflict():
    msg = "北京"
    p = proposal("agent_loop", "pending_clarification", "single_tool",
                 "read_only", msg)
    f = ShadowHostFacts(
        has_plan=False, pending_action="mcp_reference_lookup",
        pending_field="term", pending_original_request_bound=True,
        source_provenance_valid=True, tool_registered=True, tool_schema_valid=True)
    r = verify_core_evidence(core("pending_slot", "invoke"), p, msg, f)
    assert r.disposition == "potentially_admissible"
    assert r.host_authorization is False
    conflicting = p.model_copy(update={
        "reference": ShadowReference(kind="pending_slot", pending_field="wrong")})
    denied = verify_core_evidence(core("pending_slot", "invoke"),
                                  conflicting, msg, f)
    assert denied.disposition == "deny"
    untrusted = verify_core_evidence(
        core("pending_slot", "invoke"), p, msg,
        ShadowHostFacts(has_plan=False, pending_action="mcp_reference_lookup",
                        pending_field="term"))
    assert untrusted.disposition != "potentially_admissible"
    unknown_tool = verify_core_evidence(
        core("pending_slot", "invoke"), p, msg,
        ShadowHostFacts(has_plan=False, pending_action="lookup_weather",
                        pending_field="city", pending_original_request_bound=True,
                        source_provenance_valid=True, tool_registered=True,
                        tool_schema_valid=True))
    assert unknown_tool.disposition == "needs_evidence"
    assert unknown_tool.reason == "pending_tool_effect_unknown"


def test_model_cannot_rewrite_core_route_or_tool_target():
    msg = "用计算器算一下"
    plan = proposal("plan_create", "none", "none", "mutation_requested", msg)
    assert verify_core_evidence(core("tool", "invoke"), plan, msg,
                                ShadowHostFacts(has_plan=False)).reason == "core_route_conflict"
    pending = proposal("agent_loop", "pending_clarification", "single_tool",
                       "read_only", msg)
    assert verify_core_evidence(core("tool", "invoke"), pending, msg,
                                ShadowHostFacts(has_plan=False)).reason == "core_target_conflict"


def test_missing_or_repeated_or_forged_quotes_fail_closed():
    msg = "第一步第一步"
    p = proposal("plan_execute_first", "saved_plan", "first_step",
                 "read_only", "第一步")
    f = ShadowHostFacts(has_plan=True, plan_states=("pending",), revision_valid=True)
    assert verify_core_evidence(core("plan", "execute_first"), p, msg, f).disposition == "deny"
    forged = proposal("plan_execute_first", "saved_plan", "first_step",
                      "read_only", "不存在")
    assert verify_core_evidence(core("plan", "execute_first"), forged, msg, f).disposition == "deny"
    absent = ShadowRoute(schema_version="s25-route-v2",
                         primary_route="plan_execute_first", route_target="saved_plan",
                         execution_scope="first_step", effect_intent="read_only",
                         ambiguity="clear")
    assert verify_core_evidence(core("plan", "execute_first"), absent, msg, f).disposition == "needs_evidence"
