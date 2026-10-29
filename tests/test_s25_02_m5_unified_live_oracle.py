"""Offline oracle contract tests. Never represent these as real Qwen3 results."""
import pytest

from evals.s25_02_m5_unified_live import expected


@pytest.mark.parametrize("case,reason,plan,tools", [
    ("CREATE_A", "plan_created_pending_execution",
     ["pending", "pending"], []),
    ("FIRST", "partial_first_step_verified",
     ["completed", "pending"], [{"type": "tool", "name": "calculate", "ok": True}]),
    ("RESUME", "plan_completed",
     ["completed", "completed"], [{"type": "tool", "name": "calculate", "ok": True}]),
    ("CANCEL", "plan_cancelled", [], []),
])
def test_live_oracle_accepts_correct_shape(case, reason, plan, tools):
    body = {"status": "completed" if case == "CANCEL" else "partial",
            "termination_reason": reason, "plan": [{"status": x} for x in plan],
            "trace": tools}
    assert expected(case, 200, body, {"primary": 1, "plan_intent": 0})


def test_live_oracle_never_counts_two_primary_routes():
    body = {"status": "partial", "termination_reason": "plan_created_pending_execution",
            "plan": [{"status": "pending"}, {"status": "pending"}], "trace": []}
    assert not expected("CREATE_A", 200, body, {"primary": 2, "plan_intent": 0})
    assert not expected("CREATE_A", 200, body, {"primary": 1, "plan_intent": 1})


def test_live_oracle_denies_first_step_over_execution():
    body = {"status": "partial", "plan": [{"status": "completed"},
                                           {"status": "completed"}],
            "trace": [{"type": "tool", "name": "calculate", "ok": True},
                      {"type": "tool", "name": "calculate", "ok": True}]}
    assert not expected("FIRST", 200, body, {"primary": 1, "plan_intent": 0})


def test_live_oracle_denies_ordinary_without_tool():
    assert not expected("ORDINARY", 200, {"status": "completed", "trace": []},
                        {"primary": 1, "plan_intent": 0})


def test_live_oracle_requires_replay_409():
    assert expected("REPLAY", 409, {"detail": "duplicate_request_id"}, None)
    assert not expected("REPLAY", 200, {"status": "completed"}, None)
