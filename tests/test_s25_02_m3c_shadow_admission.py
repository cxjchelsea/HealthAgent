"""M3-C bridge regression: a diagnostic proposal cannot grant execution."""
from agent.m3_turn_capture import capture_turn
from agent.m3c_shadow_admission import preview_m3c_admission
from agent.route_shadow import ShadowRoute


def _route(kind, target, scope, effect):
    return ShadowRoute.model_validate({
        "schema_version": "s25-route-v2",
        "primary_route": kind,
        "route_target": target,
        "execution_scope": scope,
        "effect_intent": effect,
        "ambiguity": "clear",
    })


def _capture(checkpoint=None):
    return capture_turn(
        conversation_id="conversation", request_id="request", turn_id="turn",
        checkpoint=checkpoint, pending_action=None, clarification_id=None,
        pending_question=None)


def test_saved_plan_shadow_does_not_infer_execution_authority():
    checkpoint = {"revision": 1, "steps": [{"id": 1, "status": "pending"}]}
    result = preview_m3c_admission(
        _route("plan_execute_first", "saved_plan", "first_step", "read_only"),
        _capture(checkpoint), checkpoint=checkpoint,
        current_turn_source_verified=True, effect_authorized=True,
        tool_registered=True, tool_arguments_valid=True,
        plan_schema_valid=True, checkpoint_binding_verified=True)
    assert result["shadow_only"] is True
    assert result["execution_authorized"] is False
    assert result["disposition"] == "deny"
    assert result["reason"] == "concurrency_or_idempotency_unverified"


def test_no_plan_shadow_denies_saved_plan_execution():
    result = preview_m3c_admission(
        _route("plan_resume", "saved_plan", "remaining_steps", "read_only"),
        _capture(), checkpoint=None)
    assert result["execution_authorized"] is False
    assert result["disposition"] == "deny"


def test_text_response_shadow_never_grants_execution():
    result = preview_m3c_admission(
        _route("respond", "none", "none", "none"), _capture(), checkpoint=None)
    assert result["disposition"] == "noop"
    assert result["execution_authorized"] is False
