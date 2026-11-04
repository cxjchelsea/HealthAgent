"""Frozen AC1–AC5 v2 evaluator contract tests; no Qwen is mocked as proof.

These are tests of the *scorer*, not a substitute for physical model runs.
"""
from evals import primary_ac1_ac5_business_v2 as v2


def test_v2_manifest_preserves_all_core_operations_and_new_pending_protocol():
    names = [c[0] for c in v2.CASES]
    assert len(names) == len(set(names))
    required = {
        "PLAN_CREATE_A", "PLAN_FIRST_A", "PLAN_RESUME_A",
        "PLAN_CREATE_B", "PLAN_REVISE_B", "PLAN_CANCEL_B",
        "ORDINARY_CALCULATE", "TASK_WRITE", "TASK_REPLAY",
        "MEMORY_DENY", "MEMORY_ALLOW",
        "PENDING_TASK_ORIGIN", "PENDING_TASK_ANSWER",
        "PENDING_TASK_CONFIRM", "PENDING_TASK_REPLAY",
        "PENDING_CANCEL_ORIGIN", "PENDING_CANCEL_REPLY",
        "PENDING_TOPIC_ORIGIN", "PENDING_TOPIC_REPLY",
        "PENDING_MCP_ORIGIN", "PENDING_MCP_ANSWER", "UNKNOWN_PLAN",
    }
    assert set(names) == required
    assert names.index("PENDING_TASK_ANSWER") < names.index("PENDING_TASK_CONFIRM")
    assert names.index("PENDING_TASK_CONFIRM") < names.index("PENDING_TASK_REPLAY")
    assert v2.ROUTES["PENDING_TASK_ANSWER"] == (
        "clarify_response", "allow", "pending_write_confirmation_required")
    assert v2.ROUTES["PENDING_TOPIC_REPLY"] == (
        "respond", "allow", "model_final")
    assert len(v2.frozen_sha()) == 64


def test_pending_title_stage_requires_candidate_and_no_tool_effects():
    name = "PENDING_TASK_ANSWER"
    ev = {
        "schema_version": "primary-route-v1",
        "semantic_attempts": 1,
        "route_kind": "clarify_response",
        "disposition": "allow",
    }
    body = {
        "status": "partial",
        "termination_reason": "pending_write_confirmation_required",
        "confirmation": {
            "authority_id": "synthetic", "confirmation_nonce": "synthetic",
            "candidate_digest": "synthetic", "candidate_title": "synthetic",
        },
    }
    physical = {"turn_evidence_ok": True, "effect_ok": True}
    assert v2.assess(name, 200, body, ev, {"primary": 1}, "1", [],
                     [], {}, physical)
    assert not v2.assess(name, 200, {**body, "confirmation": None},
                         ev, {"primary": 1}, "1", [], [], {}, physical)
    assert not v2.assess(name, 200, body, ev, {"primary": 1}, "1",
                         [{"name": "create_task", "ok": True}],
                         [], {}, physical)
    assert not v2.assess(name, 200, body, ev, {"primary": 1}, "1",
                         [], [], {}, {"turn_evidence_ok": True, "effect_ok": False})


def test_structured_confirmation_has_no_model_routing_and_is_not_chat():
    body = {"status": "completed",
            "termination_reason": "pending_confirmed_task_created"}
    physical = {"effect_ok": True}
    assert v2.assess("PENDING_TASK_CONFIRM", 200, body, None, {}, "0",
                     [], [], {}, physical)
    assert not v2.assess("PENDING_TASK_CONFIRM", 200, body, None,
                         {"primary": 1}, "1", [], [], {}, physical)
    assert not v2.assess("PENDING_TASK_CONFIRM", 200, body, None,
                         {}, "0", [], [], {}, {"effect_ok": False})


def test_replay_rejects_any_database_mutation_or_semantic_call():
    data = {"detail": "duplicate_request_id"}
    ok = {"unchanged": True}
    assert v2.assess("PENDING_TASK_REPLAY", 409, data, None, {}, "0",
                     [], [], {}, ok)
    assert not v2.assess("PENDING_TASK_REPLAY", 409, data, None, {}, "0",
                         [], [], {}, {"unchanged": False})
    assert not v2.assess("PENDING_TASK_REPLAY", 200, data, None, {}, "0",
                         [], [], {}, ok)
    assert not v2.assess("PENDING_TASK_REPLAY", 409, data, None, {"primary": 1},
                         "0", [], [], {}, ok)
