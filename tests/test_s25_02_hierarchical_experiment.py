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


def test_operation_contract_differentiates_chat_generation_and_persistence():
    from agent.route_semantic_hierarchical import OP_PROMPT
    assert "Generating new text is answer, not create" in OP_PROMPT
    assert "create means establishing a durable task" in OP_PROMPT


def test_model_selected_operation_is_never_silently_overridden():
    provider = FakeProvider(['{"object_type":"none"}', '{"operation":"create"}'])
    result = asyncio.run(classify_hierarchical_diagnostic(
        provider, "qwen3:14b", "写一个不保存的邮件草稿", facts(), 5))
    assert result.object_type == "none"
    assert result.operation is None
    assert result.failure_stage == "operation"
    assert result.error_class == "ValidationError"


def test_legacy_adapter_fails_closed_for_out_of_scope_operation():
    provider = FakeProvider(['{"object_type":"none"}', '{"operation":"create"}'])
    with pytest.raises(HierarchicalStageError) as error:
        asyncio.run(classify_hierarchical(
            provider, "qwen3:14b", "写一个不保存的邮件草稿", facts(), 5))
    assert error.value.stage == "operation"
    assert error.value.object_type == "none"


def test_second_stage_schema_is_scoped_by_model_selected_object():
    from agent.route_semantic_hierarchical import OPERATION_BY_OBJECT
    expected = {
        "none": {"answer", "clarify"},
        "task": {"create"},
        "tool": {"invoke"},
        "plan": {"create", "execute_first", "resume", "cancel"},
        "plan_step": {"revise"},
        "pending_slot": {"invoke", "cancel"},
    }
    for object_type, allowed in expected.items():
        schema = OPERATION_BY_OBJECT[object_type].model_json_schema()
        operation_field = schema["properties"]["operation"]
        values = operation_field.get("enum", [operation_field.get("const")])
        assert set(values) == allowed


def test_out_of_scope_model_operation_is_reported_not_rewritten():
    provider = FakeProvider(['{"object_type":"tool"}', '{"operation":"answer"}'])
    result = asyncio.run(classify_hierarchical_diagnostic(
        provider, "qwen3:14b", "用计算器算出结果", facts(), 5))
    assert result.object_type == "tool"
    assert result.operation is None
    assert result.failure_stage == "operation"
    assert result.error_class == "ValidationError"


def test_plan_scope_still_leaves_first_vs_resume_to_model():
    provider = FakeProvider(['{"object_type":"plan"}', '{"operation":"resume"}'])
    result = asyncio.run(classify_hierarchical_diagnostic(
        provider, "qwen3:14b", "只执行第一步", facts(plan=True), 5))
    assert (result.object_type, result.operation) == ("plan", "resume")


def test_curated_boundary_sentences_are_absent_from_classifier_prompts():
    from agent.route_semantic_hierarchical import OBJECT_PROMPT, OP_PROMPT, PLAN_SCOPE_PROMPT
    from evals.s25_02_m2_hierarchical_boundary_cases import CASES
    from evals.s25_02_m2_semantic_evidence_live import QUOTE_PROMPT
    combined = "\n".join((OBJECT_PROMPT, OP_PROMPT, PLAN_SCOPE_PROMPT, QUOTE_PROMPT))
    for case in CASES:
        assert case.prompt not in combined
    for fragment in ("从第一阶段开始", "只执行第一步", "先运行第一项", "接着执行剩余步骤"):
        assert fragment not in combined


def test_plan_scope_guidance_is_sent_only_for_plan_object():
    from agent.route_semantic_hierarchical import PLAN_SCOPE_PROMPT

    for object_type, operation in (("plan", "execute_first"), ("tool", "invoke")):
        provider = FakeProvider([
            '{"object_type":"' + object_type + '"}',
            '{"operation":"' + operation + '"}',
        ])
        result = asyncio.run(classify_hierarchical_diagnostic(
            provider, "qwen3:14b", "只执行第一阶段", facts(plan=True), 5))
        assert result.failure_stage is None
        system_prompt = provider.calls[1]["messages"][0]["content"]
        assert (PLAN_SCOPE_PROMPT in system_prompt) is (object_type == "plan")


def test_first_step_scope_is_not_overridden_by_host_plan_state():
    provider = FakeProvider(['{"object_type":"plan"}', '{"operation":"execute_first"}'])
    result = asyncio.run(classify_hierarchical_diagnostic(
        provider, "qwen3:14b", "先做计划的第一步", facts(plan=True), 5))
    assert (result.object_type, result.operation) == ("plan", "execute_first")
    assert len(provider.calls) == 2
