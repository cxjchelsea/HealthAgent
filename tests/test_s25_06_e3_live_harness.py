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
