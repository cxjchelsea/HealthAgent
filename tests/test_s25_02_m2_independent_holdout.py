"""Gate and fixture-integrity tests for the holdout intake (no live model)."""
import hashlib
import json

import pytest

from evals.s25_02_m2_independent_holdout import read_sealed_cases


def fixture():
    return [{
        "case_id": f"new_{i:03}",
        "prompt": f"Review text {i}",
        "expected_object": "none",
        "expected_operation": "answer",
        "expected_disposition": "safe_non_effect",
        "critical": i % 7 == 0,
        "facts": {"has_plan": False},
    } for i in range(60)]


def save(path, cases, source_kind="independent_reviewer_sealed"):
    digest = hashlib.sha256(json.dumps(cases, ensure_ascii=False,
       sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    path.write_text(json.dumps({"source_kind": source_kind, "cases": cases,
        "sha256_canonical_cases": digest}), encoding="utf-8")


def test_accepts_valid_sealed_synthetic_contract(tmp_path):
    p = tmp_path / "holdout.json"
    save(p, fixture())
    parsed, digest = read_sealed_cases(p)
    assert len(parsed) == 60
    assert parsed[0].facts.has_plan is False
    assert len(digest) == 64


def test_rejects_manifest_tampering(tmp_path):
    p = tmp_path / "holdout.json"
    save(p, fixture())
    data = json.loads(p.read_text(encoding="utf-8"))
    data["cases"][0]["prompt"] = "modified after sealing"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_mismatch"):
        read_sealed_cases(p)


def test_rejects_small_cohort_and_fake_provenance(tmp_path):
    p = tmp_path / "holdout.json"
    save(p, fixture()[:59])
    with pytest.raises(ValueError, match="insufficient"):
        read_sealed_cases(p)
    save(p, fixture(), source_kind="author_modified_development")
    with pytest.raises(ValueError, match="origin_required"):
        read_sealed_cases(p)


def test_rejects_duplicate_cases(tmp_path):
    p = tmp_path / "holdout.json"
    cases = fixture()
    cases[1]["case_id"] = cases[0]["case_id"]
    save(p, cases)
    with pytest.raises(ValueError, match="duplicate"):
        read_sealed_cases(p)


def test_rejects_invalid_object_operation_pair(tmp_path):
    p = tmp_path / "holdout.json"
    cases = fixture()
    cases[0]["expected_object"] = "task"
    cases[0]["expected_operation"] = "resume"
    save(p, cases)
    with pytest.raises(ValueError, match="invalid_case_contract"):
        read_sealed_cases(p)


def test_rejects_missing_required_host_state(tmp_path):
    p = tmp_path / "holdout.json"
    cases = fixture()
    cases[0]["facts"] = {}
    save(p, cases)
    with pytest.raises(ValueError, match="invalid_host_facts"):
        read_sealed_cases(p)


def test_rejects_absent_or_implicit_critical_labels(tmp_path):
    p = tmp_path / "holdout.json"
    cases = fixture()
    del cases[0]["critical"]
    save(p, cases)
    with pytest.raises(ValueError, match="critical_annotation_required"):
        read_sealed_cases(p)


def test_rejects_zero_critical_coverage(tmp_path):
    p = tmp_path / "holdout.json"
    cases = fixture()
    for case in cases:
        case["critical"] = False
    save(p, cases)
    with pytest.raises(ValueError, match="no_critical_cases"):
        read_sealed_cases(p)
