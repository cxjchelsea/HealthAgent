"""Stubbed 3-call diagnostic, schema and fail-closed E2E boundaries."""
import asyncio
import json
from types import SimpleNamespace

from agent.route_shadow import ShadowRoute
from evals.s25_02_m2_semantic_evidence_live import evaluate_case


class Provider:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.replies)


def case():
    return SimpleNamespace(
        case_id="start_first", prompt="请先执行第一步",
        expected_object="plan", expected_operation="execute_first",
        facts=SimpleNamespace(
            has_plan=True, plan_states=("pending",),
            pending_action=None, pending_field=None,
            pending_original_request_bound=False,
            revision_valid=True, bound_step_valid=True,
            current_read_bound=True, source_provenance_valid=True,
            tool_registered=True, tool_schema_valid=True,
        ),
    )


def shadow(message):
    return ShadowRoute(
        schema_version="s25-route-v2",
        primary_route="plan_execute_first", route_target="saved_plan",
        execution_scope="first_step", effect_intent="read_only", ambiguity="clear",
        source_quote_evidence=[{"exact_text": message}],
    ).model_dump_json()


def test_full_chain_preserves_preview_only():
    c = case()
    p = Provider(['{"object_type":"plan"}',
                  '{"operation":"execute_first"}', shadow(c.prompt)])
    result = asyncio.run(evaluate_case(p, "qwen3:14b", c, 5))
    assert len(p.calls) == 3
    assert result["core_correct"]
    assert result["disposition"] == "potentially_admissible"
    assert result["host_authorization"] is False
    assert result["evidence_state"] == "reviewed"


def test_model_route_conflict_never_rewrites_core():
    c = case()
    wrong = ShadowRoute(schema_version="s25-route-v2",
        primary_route="respond", route_target="none",
        execution_scope="none", effect_intent="none", ambiguity="clear").model_dump_json()
    p = Provider(['{"object_type":"plan"}',
                  '{"operation":"execute_first"}', wrong])
    result = asyncio.run(evaluate_case(p, "qwen3:14b", c, 5))
    assert result["reason"] == "core_route_conflict"
    assert result["host_authorization"] is False


def test_invalid_evidence_schema_and_partial_core_fail_closed():
    c = case()
    p = Provider(['{"object_type":"plan"}',
                  '{"operation":"execute_first"}', '{"malformed":true}'])
    result = asyncio.run(evaluate_case(p, "qwen3:14b", c, 5))
    assert result["evidence_state"] == "unavailable"
    assert result["disposition"] == "deny"
    p2 = Provider(['{"object_type":"plan"}', '{"operation":"invoke"}'])
    result2 = asyncio.run(evaluate_case(p2, "qwen3:14b", c, 5))
    assert len(p2.calls) == 2
    assert result2["evidence_state"] == "not_attempted"
    assert result2["host_authorization"] is False


def test_quote_observability_without_host_authority():
    c = case()
    p = Provider(['{"object_type":"plan"}', '{"operation":"execute_first"}',
                  shadow(c.prompt)])
    result = asyncio.run(evaluate_case(p, "qwen3:14b", c, 5))
    assert result["quote_evidence_count"] == 1
    assert result["model_route"] == "plan_execute_first"
    assert result["evidence_state"] == "reviewed"
    assert result["host_authorization"] is False
    assert "SECOND-STAGE EVIDENCE" in p.calls[2]["messages"][0]["content"]
