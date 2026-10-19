"""S2-B5 evaluation runner must exercise the production HTTP path."""
from evals.s2b5_benchmark import evaluate


def test_s2b5_scripted_api_harness():
    report = evaluate()
    assert report["mode"] == "scripted_fixture_not_real_model"
    assert report["total"] == 11
    assert report["passed"] == report["total"], report["results"]
    assert report["pass_rate"] == 1.0
    assert all(row["error_type"] is None for row in report["results"])
