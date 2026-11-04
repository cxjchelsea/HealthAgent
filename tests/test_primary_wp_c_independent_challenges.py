"""Independent challenge suite is frozen and rejects false-positive outcomes."""
from evals.primary_wp_c_independent_challenges import (
    CASES, EXPECTED, assess,
)


def evidence(route, disposition):
    return {"schema_version": "primary-route-v1", "semantic_attempts": 1,
            "route_kind": route, "disposition": disposition}


def test_cases_are_independent_of_prior_phase3_samples_and_grouped():
    from evals.primary_wp_c_phase3_live import CASES as OLD
    old = {case[2] for case in OLD}
    assert len(CASES) == 11
    assert len({c[0] for c in CASES}) == len(CASES)
    assert set(c[2] for c in CASES).isdisjoint(old)
    assert len({c[1] for c in CASES}) == 5
    assert not any(c[3] for c in CASES)
    assert len(EXPECTED) == 7


def test_pending_task_cancellation_cannot_be_counted_as_successful_write():
    result = {"status": "completed", "termination_reason": "model_final"}
    assert assess("TASK_CANCEL_REPLY", 200, result,
                  evidence("respond", "allow"), {"primary": 1}, "1", [])
    assert not assess("TASK_CANCEL_REPLY", 200,
                      {"status": "completed", "termination_reason": "pending_task_created"},
                      evidence("clarify_response", "allow"), {"primary": 1}, "1",
                      [{"name": "create_task", "ok": True}])
    assert not assess("TASK_CANCEL_REPLY", 200, result,
                      evidence("respond", "allow"), {"primary": 1}, "1",
                      [{"name": "create_task", "ok": True}])
    assert not assess("TASK_CANCEL_REPLY", 200, result,
                      evidence("respond", "allow"),
                      {"primary": 1, "pending_reply": 1}, "1", [])


def test_pending_replay_requires_zero_model_and_tools():
    duplicate = {"detail": "duplicate_request_id"}
    assert assess("TASK_REPLAY_N", 409, duplicate, None, {}, "0", [])
    assert not assess("TASK_REPLAY_N", 409, duplicate, None, {}, "1", [])
    assert not assess("MCP_REPLAY_N", 409, duplicate, None, {}, "0",
                      [{"name": "mcp_reference_lookup", "ok": True}])
    assert not assess("MCP_REPLAY_N", 200, duplicate, None, {}, "0", [])


def test_authorized_task_and_mcp_need_exact_host_evidence():
    for case, route, disposition, reason, tools in [
        ("TASK_ANSWER_N", "clarify_response", "allow", "pending_task_created",
         [{"name": "create_task", "ok": True}]),
        ("MCP_ANSWER_N", "clarify_response", "allow",
         "verified_clarification_execution",
         [{"name": "mcp_reference_lookup", "ok": True}]),
    ]:
        data = {"status": "completed", "termination_reason": reason}
        assert assess(case, 200, data, evidence(route, disposition),
                      {"primary": 1}, "1", tools)
        assert not assess(case, 200, data, evidence("respond", "allow"),
                          {"primary": 1}, "1", tools)
        assert not assess(case, 200, data, evidence(route, disposition),
                          {"primary": 1}, "1", [])


def test_unknown_plan_and_memory_denial_are_not_generic_refusals():
    for case, route, disposition, reason in [
        ("NO_SAVED_PLAN_N", "plan_missing", "restrict",
         "primary_v1_missing_plan_status"),
        ("MEMORY_NO_CONSENT_N", "ordinary_tool", "deny",
         "primary_v1_memory_consent_required"),
    ]:
        data = {"status": "partial", "termination_reason": reason}
        assert assess(case, 200, data, evidence(route, disposition),
                      {"primary": 1}, "1", [])
        assert not assess(case, 200, data, evidence("reject", "restrict"),
                          {"primary": 1}, "1", [])
