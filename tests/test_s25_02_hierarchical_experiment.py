"""Hierarchical classifier respects diagnostic-only separation."""
import asyncio
from types import SimpleNamespace

import pytest

from agent.route_semantic_hierarchical import (
    ObjectChoice, OperationChoice,
    classify_hierarchical, classify_hierarchical_diagnostic, HierarchicalStageError,
)
from evals.s25_02_m2_ab_comparison import CASES, core_match
from evals.s25_02_m2_hierarchical_live import summarize

def test_object_and_operation_schemas_separate():
    assert "operation" not in ObjectChoice.model_fields
    assert "object_type" not in OperationChoice.model_fields


def test_hierarchical_comparison_expected_targets():
    plan=next(c for c in CASES if c.case_id=="plan_revision")
    assert core_match(plan,"plan_step","revise")
    assert not core_match(plan,"task","revise")


def test_no_authority_fields_in_classifier():
    for cls in (ObjectChoice, OperationChoice):
        assert "host_authorization" not in cls.model_fields
        assert "authorized" not in cls.model_fields



class FakeProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def facts(*, pending=False, plan=False):
    return SimpleNamespace(
        pending_action="calculator" if pending else None,
        pending_field="expression" if pending else None,
        pending_original_request_bound=bool(pending),
        has_plan=plan,
        plan_states=[1, 2] if plan else [],
    )


def test_both_stages_use_model_without_rewriting_semantics():
    provider = FakeProvider(['{"object_type":"plan"}',
                             '{"operation":"resume"}'])
    result = asyncio.run(classify_hierarchical(
        provider, "qwen3:14b", "继续剩余步骤", facts(), 5))
    assert (result.object_type, result.operation) == ("plan", "resume")
    assert len(provider.calls) == 2
    assert "saved_plan_exists" in provider.calls[0]["messages"][1]["content"]


def test_pending_is_bound_context_not_forced_route():
    provider = FakeProvider(['{"object_type":"tool"}',
                             '{"operation":"invoke"}'])
    result = asyncio.run(classify_hierarchical(
        provider, "qwen3:14b", "另外计算另一道题", facts(pending=True), 5))
    assert (result.object_type, result.operation) == ("tool", "invoke")
    assert "pending_tool_request_bound" in provider.calls[0]["messages"][1]["content"]


def test_stage_one_survives_stage_two_failure():
    provider = FakeProvider(['{"object_type":"plan"}', TimeoutError()])
    result = asyncio.run(classify_hierarchical_diagnostic(
        provider, "qwen3:14b", "恢复计划", facts(plan=True), 5))
    assert result.object_type == "plan"
    assert result.operation is None
    assert result.failure_stage == "operation"
    assert result.error_class == "TimeoutError"


def test_adapter_rejects_partial_stage_result():
    provider = FakeProvider(['{"object_type":"plan"}', "{}"])
    with pytest.raises(HierarchicalStageError) as error:
        asyncio.run(classify_hierarchical(provider, "qwen3:14b", "恢复计划", facts(), 5))
    assert error.value.stage == "operation"
    assert error.value.object_type == "plan"


def test_stage_summary_preserves_first_stage_and_errors():
    rows = [{
        "case_id": "case1", "object_type": "plan", "operation": None,
        "expected_object": "plan", "expected_operation": "resume",
        "object_correct": True, "operation_correct": False,
        "core_correct": False, "failure_stage": "operation",
        "core_pair": ["plan", None],
    }]
    summary = summarize(rows, ["case1"])
    assert summary["object_correct"] == 1
    assert summary["operation_failures"] == 1
    assert summary["core_accuracy"] == 0
    assert summary["object_stable_cases"] == 1


def test_stability_summary_handles_repeatable_core_pair_lists():
    rows = []
    for repeat in range(3):
        rows.append({
            "case_id": "stable", "object_type": "plan",
            "operation": "resume", "expected_object": "plan",
            "expected_operation": "resume", "object_correct": True,
            "operation_correct": True, "core_correct": True,
            "failure_stage": None, "core_pair": ["plan", "resume"],
        })
    summary = summarize(rows, ["stable"])
    assert summary["stable_cases"] == 1
    assert summary["object_stable_cases"] == 1
    assert summary["operation_stable_cases"] == 1
