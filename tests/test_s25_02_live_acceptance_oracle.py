"""Offline checks for the centralized live acceptance oracle, not Qwen evidence."""
from evals.s25_02_step5_live_acceptance import check


def test_live_oracle_rejects_claim_without_tool_observation():
    result, _ = check("F02_calculation", {"trace": [], "answer": "168", "status": "completed"})
    assert result == "FAIL"


def test_live_oracle_requires_saved_plan_first_step_only():
    result, _ = check("D03_execute_first", {
        "trace": [], "plan": [{"status": "completed"}, {"status": "completed"}]})
    assert result == "FAIL"
    result, _ = check("D03_execute_first", {
        "trace": [], "plan": [{"status": "completed"}, {"status": "pending"}]})
    assert result == "PASS"


def test_live_oracle_handles_missing_plan_gracefully():
    result, _ = check("D05_plan_revision", {
        "trace": [], "termination_reason": "plan_revised_pending_execution",
        "steps": 0, "plan": []})
    assert result == "FAIL"


def test_live_oracle_requires_failed_calculator_observation():
    result, _ = check("F04_division_zero", {"trace": [], "answer": "除数为零"})
    assert result == "FAIL"
    result, _ = check("F04_division_zero", {"trace": [
        {"type": "tool", "name": "calculate", "ok": False}]})
    assert result == "PASS"
