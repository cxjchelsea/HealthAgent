"""S25-02 M2 historical real-Qwen regression, never a blind acceptance gate.

Runs the existing real-model fixtures without altering their labels or prompts.
The 22-case set includes prompt-exposed and tuned development examples.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from evals.s25_02_m2_shadow_live import CASES, run


def classify(report: dict) -> dict:
    rows = report.get("rows", [])
    expected = {case.case_id for case in CASES}
    observed = [row.get("case_id") for row in rows]
    if not rows or len(observed) != len(set(observed)):
        regression = "HOLD"
        reason = "missing_or_duplicate_cases"
    elif set(observed) != expected:
        regression = "HOLD"
        reason = "incomplete_regression_cohort"
    elif any(row.get("m2_gate_result") != "PASS" for row in rows):
        regression = "HOLD"
        reason = "known_regression_failed"
    else:
        regression = "PASS"
        reason = "historical_regression_only"
    return {
        "cohort": "known_22_development_and_tuned_regression",
        "regression_gate": regression,
        "regression_reason": reason,
        "semantic_generalization_gate": "PENDING_INDEPENDENT_BLIND",
        "m2_gate": "HOLD",
        "unsafe_hypothetical_admissions": report.get(
            "unsafe_hypothetical_admissions"),
        "source_run_id": report.get("run_id"),
        "total": len(rows),
        "limitations": [
            "Known or prompt-exposed examples do not establish unseen generalization.",
            "Host Preview is diagnostic only, never executable authority.",
            "A separate independently authored and preregistered blind cohort is required.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="evals/results/s25-02-m2-known-regression.json")
    parser.add_argument("--classification-output",
                        default="evals/results/s25-02-m2-regression-decision.json")
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    if os.environ.get("RUN_S25_02_SHADOW_LIVE") != "1":
        parser.error("RUN_S25_02_SHADOW_LIVE=1 required")
    if args.timeout <= 0:
        parser.error("timeout must be > 0")
    # Existing runner writes a JSON report even when individual cases fail.
    status = asyncio.run(run(args))
    original = json.loads(Path(args.output).read_text(encoding="utf-8"))
    decision = classify(original)
    dest = Path(args.classification_output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(decision, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("Regression:", decision["regression_gate"])
    print("M2 acceptance:", decision["m2_gate"])
    raise SystemExit(0 if status == 0 and decision["regression_gate"] == "PASS" else 1)


if __name__ == "__main__":
    main()
