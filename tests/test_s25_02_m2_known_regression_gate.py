"""Regression evidence must never silently become independent M2 acceptance."""
from evals.s25_02_m2_known_regression import classify
from evals.s25_02_m2_shadow_live import CASES


def cases():
    return [{"case_id": case.case_id, "m2_gate_result": "PASS"} for case in CASES]


def test_known_regression_all_pass_does_not_unblock_m2():
    verdict = classify({"run_id": "x", "rows": cases(),
                        "unsafe_hypothetical_admissions": 0})
    assert verdict["regression_gate"] == "PASS"
    assert verdict["semantic_generalization_gate"] == "PENDING_INDEPENDENT_BLIND"
    assert verdict["m2_gate"] == "HOLD"


def test_failure_stays_visible():
    rows = cases()
    rows[0]["m2_gate_result"] = "HOLD"
    assert classify({"rows": rows})["regression_gate"] == "HOLD"


def test_missing_cases_are_not_claimed_pass():
    assert classify({"rows": cases()[:-1]})["regression_reason"] == (
        "incomplete_regression_cohort")


def test_duplicate_cases_are_not_claimed_pass():
    rows = cases()
    rows[-1] = rows[0].copy()
    assert classify({"rows": rows})["regression_reason"] == (
        "missing_or_duplicate_cases")


def test_empty_report_is_hold():
    assert classify({"rows": []})["m2_gate"] == "HOLD"
