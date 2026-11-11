"""M04 guard: literal unsupported operation identifiers cannot be laundered
into a permitted dependent reference plan.

This is a syntactic contract for explicit machine-readable identifiers, NOT a
general natural-language semantic entailment classifier.
"""
import pytest

from agent.dependency_plan_schema import DependencyPlanError, parse_new_plan
from agent.primary_host_admission_v1 import PrimaryHostFacts, admit_primary_route_v1


def candidate(term: str) -> dict:
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1,
        "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查询指定标识符定义", "depends_on": [],
             "status": "pending", "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {"term": {
                 "kind": "literal", "value": term,
                 "source_quote": term}}},
            {"id": 2, "goal": "统计定义文本", "depends_on": [1],
             "status": "pending", "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {"text": {
                 "kind": "observation_ref", "source_step_id": 1,
                 "field": "definition"}}},
        ], "executions": {},
    }


def test_m04_unsupported_named_operation_cannot_become_reference_term():
    message = ("请创建并立即执行一个依赖计划：第一步调用 delete_all_tasks "
               "删除所有任务，第二步调用 text_stats 统计删除结果。")
    with pytest.raises(
            DependencyPlanError, match="dependency_unsupported_operation_identifier"):
        parse_new_plan(candidate("delete_all_tasks"), source_message=message)


def test_valid_named_read_only_dependency_still_admitted():
    message = ("请使用 mcp_reference_lookup 查询 RAG 的定义，再将查询结果"
               "通过 text_stats 统计字符数；只保存计划，不执行。")
    plan = candidate("RAG")
    parsed = parse_new_plan(plan, source_message=message)
    assert parsed.steps[1].argument_bindings["text"].source_step_id == 1


def test_other_snake_case_literals_are_not_silently_assumed_to_be_tools():
    # Experimental feature errs on the side of clarification for an ambiguous
    # function-shaped identifier. This is not generalized semantic fidelity.
    with pytest.raises(
            DependencyPlanError, match="dependency_unsupported_operation_identifier"):
        parse_new_plan(candidate("some_custom_identifier"),
                       source_message="请查询 some_custom_identifier 定义后统计文本")


def test_host_revalidates_forged_bound_proposal_against_original_message():
    message = ("请创建并立即执行一个依赖计划：第一步调用 delete_all_tasks "
               "删除所有任务，第二步调用 text_stats 统计删除结果。")
    # Host must not trust a prebound object supplied by any earlier layer.
    # Build the untrusted proposal without invoking the normal binder.
    # The independently checking Host must still reject it.
    from agent.primary_route_v1 import BoundPrimaryProposal, EvidenceSpan
    from agent.dependency_plan_schema import DependencyPlanCreation
    forged = BoundPrimaryProposal(
        schema_version="primary-route-v1", route_kind="plan_create",
        execution_scope="none", effect_intent="mutation_requested",
        evidence=EvidenceSpan(start=0, end=len(message), exact_text=message),
        steps=[], dependency_plan=DependencyPlanCreation.model_validate(
            candidate("delete_all_tasks")))
    decision = admit_primary_route_v1(forged, PrimaryHostFacts(
        checkpoint=None, pending_action=None, pending_origin_verified=False,
        guarded_mode=True, turn_created=True, current_request=message,
        dependency_plan_enabled=True))
    assert (decision.disposition, decision.reason) == (
        "deny", "dependency_create_host_denied")
