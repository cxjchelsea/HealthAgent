"""Deterministic verdict checks for the official live API harness."""
from evals.s2d_conversation_api_live import _plan_checks


def test_two_turn_verdict_requires_revision_and_invalidation():
    completed = lambda expression: {
        "status": "completed", "expected_tool": "calculate",
        "expected_arguments": {"expression": expression},
        "evidence_tool": "calculate"}
    first = {"revision": 1, "steps": [completed("8*8")]}
    second = {"revision": 2, "steps": [completed("9*9")]}
    messages = [{"role": role} for role in ("user", "assistant", "user", "assistant")]
    good = _plan_checks({"http_status": 200}, {"http_status": 200},
                        first, second, messages, ["completed", "completed"])
    assert all(good.values())
    bad = _plan_checks({"http_status": 200}, {"http_status": 200},
                       first, {"revision": 2, "steps": [
                           completed("8*8"), completed("9*9")]},
                       messages, ["completed", "completed"])
    assert not bad["old_calculation_invalidated"]


def test_no_checkpoint_never_counts_as_multi_turn_success():
    checks = _plan_checks({"http_status": 503}, {"http_status": 0},
                          None, None, [], ["provider_error"])
    assert not all(checks.values())
