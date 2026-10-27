"""Diagnostic-only core compiler contract."""
from agent.route_semantic_hierarchical import HierarchicalDiagnostic
from agent.route_semantic_preview import compile_core_preview
from agent.route_shadow_host import ShadowHostFacts


def core(obj, op, failure=None):
    return HierarchicalDiagnostic(obj, op, failure)


def test_text_response_uses_existing_preview_without_authority():
    result = compile_core_preview(core("none", "answer"), ShadowHostFacts(has_plan=False))
    assert result.candidate_route == "respond"
    assert result.disposition == "safe_non_effect"
    assert result.host_authorization is False


def test_effectful_pairs_never_grant_authority_or_fabricate_arguments():
    cases = [
        ("task", "create", False), ("tool", "invoke", False),
        ("plan", "create", False), ("plan", "execute_first", True),
        ("plan", "resume", True), ("plan_step", "revise", True),
        ("pending_slot", "cancel", False), ("plan", "cancel", True),
    ]
    for obj, op, saved in cases:
        result = compile_core_preview(core(obj, op), ShadowHostFacts(has_plan=saved))
        assert result.disposition == "needs_evidence"
        assert result.host_authorization is False


def test_missing_plan_fails_closed_despite_model_saying_resume():
    assert compile_core_preview(
        core("plan", "resume"), ShadowHostFacts(has_plan=False)
    ).reason == "missing_saved_plan"


def test_pending_reply_requires_trusted_binding():
    result = compile_core_preview(core("pending_slot", "invoke"),
                                  ShadowHostFacts(has_plan=False))
    assert result.disposition == "deny"
    assert result.reason == "missing_trusted_pending_binding"


def test_partial_invalid_or_cross_object_core_fails_closed():
    facts = ShadowHostFacts(has_plan=True)
    for value in (core("plan", None, "operation"),
                  core("none", "create"), core("tool", "answer")):
        result = compile_core_preview(value, facts)
        assert result.candidate_route is None
        assert result.disposition == "deny"
        assert result.host_authorization is False
