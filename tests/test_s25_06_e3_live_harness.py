"""Offline guardrails for the E3 live harness; never substitutes for Qwen evidence."""
import json
from types import SimpleNamespace

import pytest

from evals.s25_06_e3_live import evaluate, score, _safe_issues


def test_e3_live_requires_explicit_opt_in_and_real_provider(monkeypatch):
    monkeypatch.delenv("RUN_S25_06_E3_LIVE", raising=False)
    with pytest.raises(RuntimeError, match="RUN_S25_06_E3_LIVE"):
        evaluate()
    monkeypatch.setenv("RUN_S25_06_E3_LIVE", "1")
    with pytest.raises(ValueError, match="repeats"):
        evaluate(repeats=0)
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    with pytest.raises(RuntimeError, match="real model provider"):
        evaluate()


def test_e3_live_rejects_fabricated_success_without_durable_evidence():
    response = SimpleNamespace(
        status_code=200, headers={},
        json=lambda: {
            "termination_reason": "dependency_plan_created_pending_execution",
            "answer": "SENSITIVE_DATA",
            "trace": [{"type": "tool", "name": "mcp_reference_lookup"}],
        },
    )
    state = {
        "plan": None, "attempts": [], "dispatches": [], "primary": [],
        "claims": [], "fences": [], "turns": [],
    }
    result = score("create", response, state)
    assert not result["pass"]
    assert result["attempt_count"] == 0
    assert "answer" not in result
    assert "trace" not in result
    assert "SENSITIVE_DATA" not in json.dumps(result)


def test_e3_creation_cannot_accept_legacy_or_unbound_literal_dependency():
    response = SimpleNamespace(
        status_code=200, headers={},
        json=lambda: {"termination_reason": "dependency_plan_created_pending_execution"},
    )
    plan = {
        "plan_schema_version": "dependency-plan-v1",
        "plan_executor_version": "dependency-plan-read-v1",
        "steps": [
            {"id": 1, "status": "pending", "expected_tool": "mcp_reference_lookup"},
            {"id": 2, "status": "pending", "expected_tool": "text_stats",
             "depends_on": [1], "argument_bindings": {
                 "text": {"kind": "literal", "value": "fabricated"}}},
        ],
    }
    primary = SimpleNamespace(
        route_kind="plan_create", admission_reason="dependency_create_candidate",
        disposition="allow")
    state = {
        "plan": plan, "attempts": [], "dispatches": [], "primary": [primary],
        "claims": [], "fences": [], "turns": [SimpleNamespace(state="partial")],
    }
    assert score("create", response, state)["pass"] is False
    state["plan"]["plan_schema_version"] = None
    assert score("create", response, state)["pass"] is False


def test_e3_live_only_exports_allowlisted_issue_fields():
    data = {
        "primary_v1_validation_issues": [
            {"field": "route_kind", "category": "missing",
             "input": "PRIVATE_RAW_DATA"},
            {"field": "private_key", "category": "missing"},
            {"field": "steps", "category": "PRIVATE_RAW_DATA"},
        ],
    }
    assert _safe_issues(data) == [{"field": "route_kind", "category": "missing"}]


def test_e3_resume_rejects_target_not_using_source_receipt():
    from types import SimpleNamespace as NS
    response = NS(
        status_code=200, headers={},
        json=lambda: {"termination_reason": "dependency_steps_checkpointed"},
    )
    steps = [
        {"id": 1, "status": "completed", "expected_tool": "mcp_reference_lookup"},
        {"id": 2, "status": "completed", "expected_tool": "text_stats",
         "argument_bindings": {"text": {
             "kind": "observation_ref", "source_step_id": 1, "field": "definition"}}},
    ]
    source = NS(
        id="a", status="checkpointed", tool_version="1.0.0",
        tool_name="mcp_reference_lookup", receipt_json=json.dumps({
            "ok": True, "definition": "AUTHENTIC_SOURCE"}),
        arguments_json=json.dumps({"term": "RAG"}))
    target = NS(
        id="b", status="checkpointed", tool_version="1.0.0",
        tool_name="text_stats", receipt_json=json.dumps({"ok": True}),
        arguments_json=json.dumps({"text": "WRONG_SOURCE"}))
    attempts = [
        NS(id="a", status="succeeded", tool_version="1.0.0"),
        NS(id="b", status="succeeded", tool_version="1.0.0"),
    ]
    state = {
        "plan": {
            "plan_schema_version": "dependency-plan-v1",
            "plan_executor_version": "dependency-plan-read-v1",
            "steps": steps,
        },
        "attempts": attempts, "dispatches": [source, target],
        "primary": [
            NS(disposition="allow"), NS(disposition="allow"),
            NS(disposition="allow")],
        "claims": [], "fences": [],
        "turns": [NS(state="partial"), NS(state="completed"),
                  NS(state="completed")],
    }
    assert not score("resume", response, state)["pass"]
    target.arguments_json = json.dumps({"text": "AUTHENTIC_SOURCE"})
    assert score("resume", response, state)["pass"]


def test_e3_live_preserves_safe_nested_schema_diagnostics():
    issues = {
        "primary_v1_validation_issues": [
            {"field": "dependency_plan.steps.argument_bindings",
             "category": "missing"},
            {"field": "root", "category": "other"},
            {"field": "dependency_plan.steps.private_payload",
             "category": "missing"},
        ]
    }
    assert _safe_issues(issues) == [
        {"field": "dependency_plan.steps.argument_bindings",
         "category": "missing"},
    ]


def test_e3_primary_shape_is_bounded_and_never_exports_model_contents():
    from evals.s25_06_e3_live import safe_primary_output_shape
    payload = {
        "route_kind": "plan_create", "steps": [],
        "dependency_plan": None,
        "quote": "DO_NOT_EXPORT_QUOTE",
        "arguments": {"token": "DO_NOT_EXPORT_TOKEN"},
    }
    shape = safe_primary_output_shape(json.dumps(payload))
    assert shape["route"] == "plan_create"
    assert shape["steps_shape"] == "array"
    assert shape["steps_count"] == 0
    assert shape["dependency_plan_shape"] == "null"
    assert "DO_NOT_EXPORT" not in json.dumps(shape)
    payload["dependency_plan"] = {"steps": [{}, {}],
                                  "unsafe_source": "DO_NOT_EXPORT_SOURCE"}
    shape = safe_primary_output_shape(json.dumps(payload))
    assert shape["dependency_plan_shape"] == "object"
    assert shape["dependency_steps_count"] == 2
    assert "DO_NOT_EXPORT" not in json.dumps(shape)
    assert safe_primary_output_shape("INVALID") == {"shape": "invalid_json"}



def test_e3_binding_shape_identifies_invalid_argument_names_without_leaks():
    from evals.s25_06_e3_live import safe_primary_output_shape
    payload = {
        "route_kind": "plan_create", "steps": [],
        "dependency_plan": {
            "steps": [
                {"id": 1, "expected_tool": "mcp_reference_lookup",
                 "argument_bindings": {
                     "secret_term_name": {
                         "kind": "literal", "value": "SENSITIVE_TERM",
                         "source_quote": "SENSITIVE_QUOTE"}}},
                {"id": 2, "expected_tool": "text_stats",
                 "argument_bindings": {
                     "text": {"kind": "observation_ref",
                              "source_step_id": 1, "field": "definition"}}},
            ]
        }
    }
    shape = safe_primary_output_shape(
        json.dumps(payload), "查询 SENSITIVE_TERM 的定义")
    bindings = shape["dependency_binding_shapes"]
    assert len(bindings) == 2
    assert bindings[0]["tool"] == "mcp_reference_lookup"
    assert bindings[0]["expected_key_present"] is False
    assert bindings[0]["extra_key_count"] == 1
    assert bindings[1]["expected_key_present"] is True
    assert bindings[1]["extra_key_count"] == 0
    assert bindings[1]["references_first_step"] is True
    assert bindings[1]["field_is_definition"] is True
    for secret in ("secret_term_name", "SENSITIVE_TERM", "SENSITIVE_QUOTE"):
        assert secret not in json.dumps(shape)


def test_e3_binding_shape_handles_untrusted_wrong_types_safely():
    from evals.s25_06_e3_live import safe_primary_output_shape
    payload = {
        "route_kind": "plan_create", "steps": [],
        "dependency_plan": {"steps": [
            {"id": 1, "expected_tool": ["not", "a", "name"],
             "argument_bindings": {"unsafe": {"kind": ["wrong"]}}},
            {"id": 2, "expected_tool": "text_stats",
             "argument_bindings": {"text": {"kind": ["wrong"]}}},
        ]}
    }
    shape = safe_primary_output_shape(json.dumps(payload), "RAG")
    bindings = shape["dependency_binding_shapes"]
    assert bindings[0]["tool"] == "other"
    assert bindings[1]["binding_kind"] == "missing_or_other"
    assert "not" not in json.dumps(shape)
