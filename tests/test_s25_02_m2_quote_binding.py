"""Unique-source quotation binding avoids LLM index arithmetic, but never grants authority."""
import pytest

from agent.route_shadow import ShadowRoute, bind_unique_quotes, verify_source_spans
from agent.route_shadow_host import ShadowHostFacts, preview_admission


def route(**extras):
    return ShadowRoute.model_validate({
        "schema_version":"s25-route-v2","primary_route":"plan_create",
        "route_target":"none","execution_scope":"none",
        "effect_intent":"mutation_requested","ambiguity":"clear",**extras})


def test_distinct_unicode_quotes_bind_without_changing_route():
    text="先查询 RAG，再计算 8*8"
    p=route(source_quote_evidence=[{"exact_text":"先查询 RAG"}],
            plan_step_quote_evidence=[{"exact_text":"查询 RAG"},
                                      {"exact_text":"计算 8*8"}])
    bound=bind_unique_quotes(p,text)
    verify_source_spans(bound,text)
    assert [x.exact_text for x in bound.plan_step_evidence]==["查询 RAG","计算 8*8"]
    assert bound.primary_route == "plan_create"
    assert p.plan_step_evidence == []
    assert preview_admission(bound,ShadowHostFacts(has_plan=False)).disposition == "needs_evidence"


@pytest.mark.parametrize("quote,text,reason",[
    ("RAG","查 RAG 再读 RAG","shadow_quote_ambiguous"),
    ("miss","查 RAG","shadow_quote_not_found"),
])
def test_nonunique_or_missing_quotes_are_rejected(quote,text,reason):
    with pytest.raises(ValueError,match=reason):
        bind_unique_quotes(route(source_quote_evidence=[{"exact_text":quote}]),text)


def test_forged_explicit_offsets_never_rebound():
    p=route(source_evidence=[{"start":0,"end":3,"exact_text":"查询"}],
            source_quote_evidence=[{"exact_text":"计算"}])
    with pytest.raises(ValueError,match="shadow_source_offset"):
        verify_source_spans(p,"请查询然后计算")


def test_overlap_rejected_even_with_unique_quote():
    p=route(source_evidence=[{"start":0,"end":2,"exact_text":"查询"}],
            source_quote_evidence=[{"exact_text":"查询"}])
    with pytest.raises(ValueError,match="shadow_quote_overlapping"):
        bind_unique_quotes(p,"查询")


def test_quote_binding_does_not_set_effect_or_permission():
    p=ShadowRoute.model_validate({
        "schema_version":"s25-route-v2","primary_route":"agent_loop",
        "route_target":"pending_clarification","execution_scope":"single_tool",
        "effect_intent":"none","ambiguity":"clear",
        "reference":{"kind":"pending_slot","pending_field":"term"},
        "source_quote_evidence":[{"exact_text":"RAG"}]})
    bound=bind_unique_quotes(p,"RAG")
    assert bound.effect_intent=="none"
    assert preview_admission(bound,ShadowHostFacts(
        has_plan=False,pending_action="mcp_reference_lookup",pending_field="term",
        pending_original_request_bound=True,tool_registered=True,
        tool_schema_valid=True,source_provenance_valid=True)).reason == "pending_effect_unspecified"
