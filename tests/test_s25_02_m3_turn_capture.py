"""M3 read-only Turn snapshot identity and pre-execution state telemetry."""
from agent.m3_turn_capture import capture_turn, compare_captured_identity


def test_capture_exposes_only_bounded_trusted_metadata():
    checkpoint={"revision":3,"steps":[{"status":"pending"}],
                "secret":"must_not_appear"}
    captured=capture_turn(
        conversation_id="conv",request_id="req",turn_id="turn",
        checkpoint=checkpoint,pending_action="mcp_reference_lookup",
        clarification_id="msg",pending_question="Which term?")
    out=compare_captured_identity(
        captured,conversation_id="conv",request_id="req",turn_id="turn")
    assert out["result"] == "MATCH"
    assert out["checkpoint_revision"] == 3
    assert out["pending_origin_present"] is True
    assert out["authorization_verified"] is False
    assert "secret" not in str(out)


def test_other_turn_or_request_never_matches():
    captured=capture_turn(
        conversation_id="conv",request_id="req",turn_id="turn",
        checkpoint=None,pending_action=None,clarification_id=None,
        pending_question=None)
    assert compare_captured_identity(
        captured,conversation_id="conv",request_id="req",turn_id="other"
    )["result"] == "MISMATCH"
    assert compare_captured_identity(
        captured,conversation_id="conv",request_id="other",turn_id="turn"
    )["result"] == "MISMATCH"


def test_missing_identity_never_matches():
    captured=capture_turn(
        conversation_id="conv",request_id="",turn_id="turn",
        checkpoint=None,pending_action=None,clarification_id=None,
        pending_question=None)
    assert compare_captured_identity(
        captured,conversation_id="conv",request_id="",turn_id="turn"
    )["result"] == "MISMATCH"


def test_untrusted_or_boolean_checkpoint_revision_not_accepted():
    captured=capture_turn(
        conversation_id="conv",request_id="req",turn_id="turn",
        checkpoint={"revision":True},pending_action=None,
        clarification_id=None,pending_question=None)
    assert captured.checkpoint_revision is None
