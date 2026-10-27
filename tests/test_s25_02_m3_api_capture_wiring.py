"""Verify M3-B diagnostics are opt-in, pre-execution, and not authority.

These source-order checks are a guard against accidentally moving read-only
capture to post-turn telemetry. Physical same-turn verification remains pending.
"""
from pathlib import Path


def test_capture_precedes_legacy_operation_and_finalization():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    capture = source.index("preturn_capture = capture_turn(")
    first_legacy_control = source.index("if body.goal_confirmation is not None:", capture)
    finalization = source.index("response.content = answer", capture)
    assert capture < first_legacy_control < finalization


def test_m3_telemetry_is_opt_in_and_only_in_diagnostic_output():
    source = Path("apps/api/main.py").read_text(encoding="utf-8")
    assert "if settings.route_shadow_enabled:\n                preturn_capture = capture_turn(" in source
    assert 'response_data["route_shadow"]["m3_preturn_identity"]' in source
    assert 'if shadow is not None:' in source
    assert "from agent.m3_turn_capture import capture_turn, compare_captured_identity" in source


def test_preturn_capture_cannot_grant_execution():
    from agent.m3_turn_capture import capture_turn, compare_captured_identity
    snap = capture_turn(conversation_id="c", request_id="r", turn_id="t",
                        checkpoint={"revision": 1}, pending_action=None,
                        clarification_id=None, pending_question=None)
    diagnostic = compare_captured_identity(
        snap, conversation_id="c", request_id="r", turn_id="t")
    assert diagnostic["result"] == "MATCH"
    assert diagnostic["authorization_verified"] is False
