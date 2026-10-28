"""M4 opt-in cancellation admission: candidate decision is NOT DB authority.

Only the exact current Turn and pre-turn checkpoint can be considered.
The final plan-store CAS and execution-claim guard remain mandatory.
"""
from agent.plan_control import explicit_saved_plan_cancellation
from agent.route_admission_m3 import PreTurnFacts, decide_admission
from agent.route_shadow import ShadowRoute


def admit_guarded_plan_cancel(*, message: str, conversation_id: str,
                              request_id: str, checkpoint: dict | None,
                              turn_created: bool, semantic_confirmed: bool) -> bool:
    """Permit entry to existing Host CAS cancellation, never invoke a tool.

    Model confirmation alone is insufficient. This adapter is deliberately
    scoped to cancellation; all other routes remain on the legacy path.
    """
    if not (semantic_confirmed and turn_created
            and explicit_saved_plan_cancellation(message)
            and isinstance(checkpoint, dict)):
        return False
    steps = checkpoint.get("steps")
    revision = checkpoint.get("revision")
    if (not isinstance(steps, list) or not steps
            or type(revision) is not int or revision < 1
            or any(not isinstance(s, dict)
                   or s.get("status") not in {"pending", "completed"}
                   for s in steps)):
        return False
    route = ShadowRoute.model_validate({
        "schema_version": "s25-route-v2",
        "primary_route": "plan_cancel",
        "route_target": "saved_plan",
        "execution_scope": "none",
        "effect_intent": "mutation_requested",
        "ambiguity": "clear",
        "reference": {"kind": "saved_plan"},
    })
    facts = PreTurnFacts(
        conversation_id=conversation_id,
        request_id=request_id,
        checkpoint_revision=revision,
        has_saved_plan=True,
        plan_step_states=tuple(s["status"] for s in steps),
        user_source_verified=True,
        effect_authorized=True,
        plan_schema_valid=True,
        checkpoint_binding_verified=True,
        request_is_fresh=turn_created,
        session_lock_held=turn_created,
        idempotency_authority_verified=turn_created,
    )
    return decide_admission(route, facts).disposition == "candidate_execute"
