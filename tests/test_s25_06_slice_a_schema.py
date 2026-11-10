"""S25-06 Slice A deterministic safety contract; no HTTP, DB or tool execution."""
from copy import deepcopy

import pytest

from agent.dependency_plan_schema import (
    DependencyPlanError, PLAN_SCHEMA_VERSION, classify_plan_format,
    creation_ready_step_ids, parse_new_plan,
)


MESSAGE = "请先查询 RAG 的定义，再统计查到的文本字符数。"


def proposal():
    return {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "revision": 1,
        "next_step_id": 3,
        "steps": [
            {"id": 1, "goal": "查 RAG 定义", "depends_on": [],
             "status": "pending", "expected_tool": "mcp_reference_lookup",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "term": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}}},
            {"id": 2, "goal": "统计定义字符", "depends_on": [1],
             "status": "pending", "expected_tool": "text_stats",
             "tool_version": "1.0.0",
             "argument_bindings": {
                 "text": {"kind": "observation_ref", "source_step_id": 1,
                          "field": "definition"}}},
        ],
        "executions": {},
    }


def check_rejected(p, expected, message=MESSAGE):
    with pytest.raises(DependencyPlanError) as exc:
        parse_new_plan(p, source_message=message)
    assert exc.value.code == expected


def test_valid_proposal_is_pure_and_topological():
    payload = proposal()
    before = deepcopy(payload)
    result = parse_new_plan(payload, source_message=MESSAGE)
    assert result.plan_schema_version == PLAN_SCHEMA_VERSION
    assert creation_ready_step_ids(result) == (1,)
    assert payload == before
    assert result.steps[1].argument_bindings["text"].source_step_id == 1


def test_old_format_is_classified_but_not_interpreted_as_new():
    legacy = {"revision": 1, "steps": [{"id": 1, "goal": "a", "status": "pending"}]}
    assert classify_plan_format(legacy) == "legacy"
    check_rejected(legacy, "dependency_version_mismatch")


@pytest.mark.parametrize("changed,code", [
    ({"plan_schema_version": "other"}, "dependency_version_mismatch"),
    ({"plan_schema_version": None}, "dependency_version_mismatch"),
    ({"plan_executor_version": "legacy-plan-v1"}, "dependency_invalid_schema"),
    ({"revision": 2}, "dependency_invalid_schema"),
    ({"next_step_id": 99}, "dependency_invalid_schema"),
    ({"unexpected": True}, "dependency_invalid_schema"),
    ({"executions": {"1": {"dispatch_id": "forged"}}}, "dependency_execution_not_allowed"),
    ({"executions": None}, "dependency_invalid_schema"),
])
def test_top_level_rejections(changed, code):
    p = proposal()
    p.update(changed)
    check_rejected(p, code)


@pytest.mark.parametrize("changed,code", [
    ({"id": True}, "dependency_invalid_schema"),
    ({"id": 1}, "dependency_invalid_schema"),
    ({"depends_on": [1, 1]}, "dependency_duplicate_edge"),
    ({"depends_on": [2]}, "dependency_cycle"),
    ({"depends_on": [77]}, "dependency_unknown_step"),
    ({"depends_on": []}, "dependency_source_not_dependency"),
    ({"status": "completed"}, "dependency_invalid_schema"),
    ({"tool_version": None}, "dependency_invalid_schema"),
    ({"expected_tool": "create_task"}, "dependency_invalid_schema"),
    ({"unexpected": "x"}, "dependency_invalid_schema"),
])
def test_step_rejections(changed, code):
    if code is None:
        p = proposal()
        p["steps"][1].update(changed)
        assert creation_ready_step_ids(parse_new_plan(p, source_message=MESSAGE)) == (1, 2)
        return
    p = proposal()
    p["steps"][1].update(changed)
    check_rejected(p, code)


def test_forward_dependency_rejected_even_if_acyclic():
    p = proposal()
    p["steps"][0]["depends_on"] = [2]
    check_rejected(p, "dependency_order_invalid")


def test_independent_literal_step_is_valid():
    p = proposal()
    p["steps"][1]["depends_on"] = []
    p["steps"][1]["argument_bindings"] = {
        "text": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}
    }
    assert creation_ready_step_ids(parse_new_plan(p, source_message=MESSAGE)) == (1, 2)


def test_missing_required_execution_envelope():
    p = proposal()
    del p["executions"]
    check_rejected(p, "dependency_invalid_schema")


def test_wrong_next_id_and_missing_dependency_list():
    p = proposal()
    p["next_step_id"] = 4
    check_rejected(p, "dependency_invalid_schema")
    p = proposal()
    del p["steps"][1]["depends_on"]
    check_rejected(p, "dependency_invalid_schema")


@pytest.mark.parametrize("bindings,code", [
    ({"text": {"kind": "observation_ref", "source_step_id": 1, "field": "raw"}},
     "dependency_invalid_schema"),
    ({"text": {"kind": "observation_ref", "source_step_id": True, "field": "definition"}},
     "dependency_invalid_schema"),
    ({"text": {"kind": "observation_ref", "source_step_id": 99, "field": "definition"}},
     "dependency_source_not_dependency"),
    ({"term": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}},
     "dependency_invalid_arguments"),
    ({"text": {"kind": "literal", "value": "imagined", "source_quote": "RAG"}},
     "dependency_source_unbound"),
    ({"text": {"kind": "literal", "value": "x", "source_quote": "not quoted"}},
     "dependency_source_unbound"),
    ({"text": {"kind": "literal", "value": "x" * 4001, "source_quote": "x"}},
     "dependency_invalid_schema"),
])
def test_binding_rejections(bindings, code):
    p = proposal()
    p["steps"][1]["argument_bindings"] = bindings
    check_rejected(p, code)


def test_source_field_rejected_for_wrong_tool():
    p = proposal()
    # A step may reference only the allowed output of the exact upstream tool.
    p["steps"][0]["expected_tool"] = "text_stats"
    p["steps"][0]["argument_bindings"] = {
        "text": {"kind": "literal", "value": "RAG", "source_quote": "RAG"}}
    check_rejected(p, "dependency_output_not_allowed")


def test_unbound_literal_and_invalid_source_message():
    p = proposal()
    p["steps"][0]["argument_bindings"]["term"]["source_quote"] = "imagined"
    check_rejected(p, "dependency_source_unbound")
    check_rejected(proposal(), "dependency_source_unbound", message="")


def test_new_field_without_format_must_never_fall_through_to_legacy():
    p = proposal()
    del p["plan_schema_version"]
    with pytest.raises(DependencyPlanError, match="dependency_invalid_schema"):
        classify_plan_format(p)


def test_no_new_fields_in_legacy_or_unknown_version():
    assert classify_plan_format({"steps": []}) == "legacy"
    with pytest.raises(DependencyPlanError, match="dependency_invalid_schema"):
        classify_plan_format({"plan_executor_version": "dependency-plan-read-v1"})
    with pytest.raises(DependencyPlanError, match="dependency_version_mismatch"):
        classify_plan_format({"plan_schema_version": "future-v2", "steps": []})


def test_step_count_and_next_id_are_bounded():
    p = proposal()
    p["steps"] = p["steps"][:1]
    check_rejected(p, "dependency_invalid_schema")
    p = proposal()
    p["steps"] = p["steps"] * 5
    check_rejected(p, "dependency_invalid_schema")


def test_no_optional_host_grants_or_invocation_methods_exposed():
    import agent.dependency_plan_schema as schema
    for name in ("execute_tool", "PlanReadInvoker", "Session",
                 "grant_execution", "dispatch", "create_task"):
        assert not hasattr(schema, name)
