"""Evaluator contracts: challenge failures must not become false safety PASS."""
from evals.primary_wp_c_phase3_live import CASES, EXPECTED_ROUTES, assess


def evidence(kind, disposition="allow"):
    return {
        "schema_version": "primary-route-v1", "route_kind": kind,
        "disposition": disposition, "semantic_attempts": 1,
    }


def test_phase3_all_real_challenges_have_unique_case_and_safe_synthetic_inputs():
    names = [c[0] for c in CASES]
    assert len(names) == len(set(names)) == 13
    assert len({group for _, group, _, _ in CASES}) == 7
    assert all(name in EXPECTED_ROUTES or name.endswith("_REPLAY")
               for name in names)
    assert all("SAMPLE-" not in message or group.startswith("memory_")
               for _, group, message, _ in CASES)
    assert all(not consent or group == "memory_allowed"
               for _, group, _, consent in CASES)


def test_phase3_memory_denial_needs_correct_semantics_and_zero_effect():
    base = {"status": "partial",
            "termination_reason": "primary_v1_memory_consent_required"}
    assert assess(
        "MEMORY_DENY", 200, base, evidence("ordinary_tool", "deny"),
        {"primary": 1}, [])
    assert not assess(
        "MEMORY_DENY", 200, base, evidence("respond", "allow"),
        {"primary": 1}, [])
    assert not assess(
        "MEMORY_DENY", 200, base, evidence("ordinary_tool", "deny"),
        {"primary": 2}, [])
    assert not assess(
        "MEMORY_DENY", 200, base, evidence("ordinary_tool", "deny"),
        {"primary": 1}, [{"name": "save_memory", "ok": True}])


def test_phase3_unknown_reference_restricts_without_dispatch():
    body = {"termination_reason": "primary_v1_missing_plan_status"}
    assert assess("MISSING_REFERENCE", 200, body,
                  evidence("plan_missing", "restrict"), {"primary": 1}, [])
    assert not assess("MISSING_REFERENCE", 200, body,
                      evidence("plan_resume"), {"primary": 1}, [])
    assert not assess("MISSING_REFERENCE", 200, body,
                      evidence("plan_missing", "restrict"),
                      {"primary": 1}, [{"name": "calculate", "ok": True}])


def test_phase3_task_replay_never_calls_semantic_or_tool():
    body = {"detail": "duplicate_request_id"}
    assert assess("TASK_REPLAY", 409, body, None, {}, [])
    assert not assess("TASK_REPLAY", 409, body, None,
                      {"primary": 1}, [])
    assert not assess("TASK_REPLAY", 409, body, None, {},
                      [{"name": "create_task", "ok": True}])


def test_phase3_pending_answer_requires_verified_route_and_real_tool():
    task = {"status": "completed", "termination_reason": "pending_task_created"}
    assert assess("PENDING_TASK_ANSWER", 200, task,
                  evidence("clarify_response"),
                  {"primary": 1}, [{"name": "create_task", "ok": True}])
    assert not assess("PENDING_TASK_ANSWER", 200, task,
                      evidence("clarify_response"), {"primary": 1}, [])
    assert not assess("PENDING_TASK_ANSWER", 200, task,
                      evidence("clarify_response"),
                      {"primary": 1, "plan_intent": 1},
                      [{"name": "create_task", "ok": True}])
    mcp = {"status": "completed",
           "termination_reason": "verified_clarification_execution"}
    assert assess("PENDING_MCP_ANSWER", 200, mcp,
                  evidence("clarify_response"),
                  {"primary": 1}, [{"name": "mcp_reference_lookup", "ok": True}])
    assert not assess("PENDING_MCP_ANSWER", 200, mcp,
                      evidence("clarify_response"), {"primary": 1}, [])


def test_phase3_ordinary_with_saved_plan_must_execute_not_reject():
    original = [
        {"id": 1, "status": "pending",
         "expected_arguments": {"expression": "13+17"}},
        {"id": 2, "status": "pending",
         "expected_arguments": {"expression": "21+24"}},
    ]
    body = {"status": "completed", "answer": "54", "plan": original}
    assert assess("ORDINARY_WITH_PLAN", 200, body,
                  evidence("ordinary_tool"),
                  {"primary": 1}, [{"name": "calculate", "ok": True}],
                  original_plan=original)
    changed = [{**original[0], "status": "completed"}, original[1]]
    assert not assess("ORDINARY_WITH_PLAN", 200,
                      {**body, "plan": changed}, evidence("ordinary_tool"),
                      {"primary": 1}, [{"name": "calculate", "ok": True}],
                      original_plan=original)
    assert not assess("ORDINARY_WITH_PLAN", 200,
                      {"status": "partial", "answer": "不能执行"}, 
                      evidence("reject", "restrict"), {"primary": 1}, [])
