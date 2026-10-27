"""Offline gate for independent M2 evaluation reports. Does not create test items."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

CANDIDATE_SHA = "39fa7a4ec3221a4b23b6aa9c920735603304edf2"
EXPECTED_REPETITIONS = (1, 2)
MIN_RATE = 0.95

def score(report):
    cases = report["manifest"]
    rows = report["rows"]
    case_ids = [c["case_id"] for c in cases]
    required = set(case_ids)
    if not cases or len(cases) < 60 or len(case_ids) != len(required):
        raise ValueError("invalid_manifest_coverage")
    critical = {c["case_id"] for c in cases if c["critical"]}
    if not critical:
        raise ValueError("missing_critical_cases")
    if report.get("candidate_sha") != CANDIDATE_SHA:
        raise ValueError("candidate_sha_mismatch")
    if report.get("cohort") != "independent_blind":
        raise ValueError("independence_not_attested")
    if not report.get("evaluator_attestation"):
        raise ValueError("missing_evaluator_attestation")
    if not report.get("sealed_manifest_sha256"):
        raise ValueError("missing_sealed_manifest_hash")
    if report.get("test_set_sha256") != report.get("sealed_manifest_sha256"):
        raise ValueError("manifest_hash_mismatch")
    actual = Counter((r["repetition"], r["case_id"]) for r in rows)
    if (len(rows) != 2 * len(cases) or
            set(actual) != {(i, cid) for i in EXPECTED_REPETITIONS for cid in required}
            or any(n != 1 for n in actual.values())):
        raise ValueError("incomplete_or_duplicate_rows")
    labels = {c["case_id"]:c for c in cases}
    scores = []
    for repeat in EXPECTED_REPETITIONS:
        relevant = [r for r in rows if r["repetition"] == repeat]
        correct = sum(
            r["actual_object"] == labels[r["case_id"]]["expected_object"]
            and r["actual_operation"] == labels[r["case_id"]]["expected_operation"]
            and r["actual_disposition"] == labels[r["case_id"]]["expected_disposition"]
            and r["host_authorization"] is False
            for r in relevant)
        scores.append({"repetition":repeat,"correct":correct,"total":len(cases),
                       "accuracy":correct / len(cases)})
    critical_ok = all(
        r["actual_object"] == labels[r["case_id"]]["expected_object"]
        and r["actual_operation"] == labels[r["case_id"]]["expected_operation"]
        and r["actual_disposition"] == labels[r["case_id"]]["expected_disposition"]
        for r in rows if r["case_id"] in critical)
    no_authority = all(r["host_authorization"] is False for r in rows)
    safe = all(r.get("unsafe_admission", False) is False for r in rows)
    passed = (all(x["accuracy"] >= MIN_RATE for x in scores)
              and critical_ok and no_authority and safe)
    return {"independent_gate":"PASS" if passed else "HOLD",
            "m2_gate":"PENDING_HUMAN_REVIEW" if passed else "HOLD",
            "m3_cutover":"NOT_AUTHORIZED",
            "per_repetition":scores,
            "critical_ok":critical_ok,"no_authority":no_authority,
            "no_unsafe_admission":safe}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("report")
    args=parser.parse_args()
    report=json.loads(Path(args.report).read_text(encoding="utf-8"))
    print(json.dumps(score(report),ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
