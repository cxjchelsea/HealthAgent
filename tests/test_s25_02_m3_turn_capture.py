"""M3 read-only Turn snapshot identity, digest and drift telemetry."""
from agent.m3_turn_capture import (
    capture_turn, compare_captured_identity, compare_checkpoint_snapshot,
)


def captured(checkpoint):
    return capture_turn(
        conversation_id="conv", request_id="req", turn_id="turn",
        checkpoint=checkpoint, pending_action="mcp_reference_lookup",
        clarification_id="msg", pending_question="Which term?")


def test_capture_exposes_only_bounded_trusted_metadata():
    snap = captured({"revision": 3, "steps": [{"status": "pending"}],
                     "secret": "must_not_appear"})
    out = compare_captured_identity(
        snap, conversation_id="conv", request_id="req", turn_id="turn")
    assert out["result"] == "MATCH"
    assert out["checkpoint_revision"] == 3
    assert out["pending_origin_present"] is True
    assert out["authorization_verified"] is False
    assert "secret" not in str(out)
    assert "checkpoint_digest" not in out


def test_other_turn_or_request_never_matches():
    snap = captured(None)
    assert compare_captured_identity(
        snap, conversation_id="conv", request_id="req", turn_id="other"
    )["result"] == "MISMATCH"
    assert compare_captured_identity(
        snap, conversation_id="conv", request_id="other", turn_id="turn"
    )["result"] == "MISMATCH"


def test_missing_identity_never_matches():
    snap = capture_turn(
        conversation_id="conv", request_id="", turn_id="turn",
        checkpoint=None, pending_action=None, clarification_id=None,
        pending_question=None)
    assert compare_captured_identity(
        snap, conversation_id="conv", request_id="", turn_id="turn"
    )["result"] == "MISMATCH"


def test_boolean_checkpoint_revision_not_accepted():
    snap = captured({"revision": True})
    assert snap.checkpoint_revision is None


def test_checkpoint_digest_is_stable_for_mapping_key_order():
    left = captured({"revision": 3, "steps": [{"id": "s1", "status": "pending"}]})
    right = compare_checkpoint_snapshot(
        left, {"steps": [{"status": "pending", "id": "s1"}], "revision": 3})
    assert right["result"] == "SAME"
    assert right["captured_revision"] == right["actual_revision"] == 3
    assert right["authorization_verified"] is False


def test_checkpoint_change_is_detected_even_if_revision_is_unchanged():
    snap = captured({"revision": 3, "steps": [{"id": "s1", "status": "pending"}]})
    changed = compare_checkpoint_snapshot(
        snap, {"revision": 3, "steps": [{"id": "s1", "status": "completed"}]})
    assert changed["result"] == "CHANGED"


def test_checkpoint_revision_change_is_detected():
    snap = captured({"revision": 3, "steps": []})
    changed = compare_checkpoint_snapshot(snap, {"revision": 4, "steps": []})
    assert changed["result"] == "CHANGED"
    assert changed["actual_revision"] == 4


def test_checkpoint_appearance_or_disappearance_is_detected():
    assert compare_checkpoint_snapshot(captured(None), {"revision": 1})["result"] == "CHANGED"
    assert compare_checkpoint_snapshot(captured({"revision": 1}), None)["result"] == "CHANGED"


def test_non_dict_checkpoint_cannot_be_proven_equal():
    snap = captured({"revision": 1})
    # A non-dict actual value is outside the checkpoint contract and is not equal evidence.
    assert compare_checkpoint_snapshot(snap, None)["result"] == "CHANGED"
