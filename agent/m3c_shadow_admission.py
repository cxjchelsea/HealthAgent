"""M3-C shadow-only bridge from semantic route to conservative preturn admission.

No executor access, no persistence, no authority grant. Unknown evidence stays false.
"""
from dataclasses import asdict

from agent.m3_turn_capture import CapturedTurn
from agent.route_admission_m3 import PreTurnFacts, decide_admission
from agent.route_shadow import ShadowRoute


def preview_m3c_admission(route: ShadowRoute, capture: CapturedTurn,
                          *, checkpoint: dict | None,
                          current_turn_source_verified: bool = False,
                          effect_authorized: bool = False,
                          tool_registered: bool = False,
                          tool_arguments_valid: bool = False,
                          plan_schema_valid: bool = False,
                          checkpoint_binding_verified: bool = False,
                          pending_origin_verified: bool = False) -> dict:
    """Conservative diagnostic: a candidate is NEVER executable authority.

    Runtime Turn ownership and request freshness are intentionally not inferred
    from a completed response or model proposal. In post-finalization shadow
    mode these proofs are absent and the decision must fail closed.
    """
    steps = checkpoint.get("steps", []) if isinstance(checkpoint, dict) else []
    states = tuple(step["status"] for step in steps
                   if isinstance(step, dict) and isinstance(step.get("status"), str))
    facts = PreTurnFacts(
        conversation_id=capture.conversation_id,
        request_id=capture.request_id,
        checkpoint_revision=capture.checkpoint_revision,
        has_saved_plan=capture.checkpoint_present,
        plan_step_states=states,
        pending_action=capture.pending_action,
        pending_original_request_verified=pending_origin_verified,
        user_source_verified=current_turn_source_verified,
        effect_authorized=effect_authorized,
        tool_registered=tool_registered,
        tool_arguments_valid=tool_arguments_valid,
        plan_schema_valid=plan_schema_valid,
        checkpoint_binding_verified=checkpoint_binding_verified,
        request_is_fresh=False,
        session_lock_held=False,
        idempotency_authority_verified=False,
    )
    result = decide_admission(route, facts)
    return {**asdict(result), "shadow_only": True, "execution_authorized": False,
            "preturn_checkpoint_revision": capture.checkpoint_revision}
