"""S25-02 M3: pure, conservative pre-turn admission contract.

This module never dispatches tools, changes persistence, or reads a legacy result.
Its output is a candidate admission, NOT executable authority. No API wiring.
"""
from dataclasses import dataclass
from typing import Literal

from agent.route_shadow import ShadowRoute


@dataclass(frozen=True)
class PreTurnFacts:
    conversation_id: str
    request_id: str
    checkpoint_revision: int | None = None
    has_saved_plan: bool = False
    plan_step_states: tuple[str, ...] = ()
    pending_action: str | None = None
    pending_field: str | None = None
    pending_original_request_verified: bool = False
    user_source_verified: bool = False
    effect_authorized: bool = False
    tool_registered: bool = False
    tool_arguments_valid: bool = False
    plan_schema_valid: bool = False
    checkpoint_binding_verified: bool = False
    request_is_fresh: bool = False
    session_lock_held: bool = False
    idempotency_authority_verified: bool = False


@dataclass(frozen=True)
class AdmissionDecision:
    disposition: Literal["deny", "clarify", "noop", "candidate_execute"]
    route: str
    reason: str
    execution_authorized: bool = False


def decide_admission(route: ShadowRoute, facts: PreTurnFacts) -> AdmissionDecision:
    """Fail closed; candidate_execute needs an independent final invoker gate."""
    def decision(status: str, reason: str) -> AdmissionDecision:
        return AdmissionDecision(status, route.primary_route, reason, False)

    if not facts.conversation_id or not facts.request_id:
        return decision("deny", "missing_request_identity")
    if route.ambiguity != "clear":
        return decision("clarify", "uncertain_intent")
    if route.primary_route == "clarify":
        return decision("clarify", "needs_user_input")
    if route.primary_route == "respond":
        if route.effect_intent != "none":
            return decision("deny", "unexpected_effect")
        return decision("noop", "text_response")
    if not facts.request_is_fresh:
        return decision("deny", "replay_or_stale_request")
    if not facts.session_lock_held or not facts.idempotency_authority_verified:
        return decision("deny", "concurrency_or_idempotency_unverified")
    if not facts.user_source_verified:
        return decision("deny", "current_turn_source_unverified")
    if route.primary_route == "cancel":
        if route.route_target == "pending_clarification":
            if not facts.pending_action or not facts.pending_original_request_verified:
                return decision("deny", "pending_cancel_binding_unverified")
            return decision("candidate_execute", "pending_cancel_requires_state_guard")
        return decision("deny", "cancel_target_unverified")
    if route.primary_route == "plan_cancel":
        if not facts.has_saved_plan or facts.checkpoint_revision is None:
            return decision("deny", "missing_plan_checkpoint")
        if not facts.checkpoint_binding_verified or not facts.plan_schema_valid:
            return decision("deny", "plan_binding_unverified")
        if not facts.effect_authorized:
            return decision("deny", "plan_cancel_write_authority_missing")
        if route.reference.kind != "saved_plan":
            return decision("deny", "plan_cancel_reference_unverified")
        return decision("candidate_execute", "requires_plan_cancel_cas")
    if route.primary_route == "agent_loop":
        if route.route_target == "pending_clarification":
            if not facts.pending_action or not facts.pending_field:
                return decision("deny", "missing_pending_binding")
            if not facts.pending_original_request_verified:
                return decision("deny", "pending_origin_unverified")
            if (route.reference.pending_field is not None
                    and route.reference.pending_field != facts.pending_field):
                return decision("deny", "pending_field_conflict")
        elif route.route_target != "tool_action":
            return decision("deny", "tool_target_missing")
        if not facts.tool_registered or not facts.tool_arguments_valid:
            return decision("deny", "tool_registry_or_schema_unverified")
        if route.effect_intent == "mutation_requested" and not facts.effect_authorized:
            return decision("deny", "write_authority_missing")
        return decision("candidate_execute", "requires_final_tool_invoker_gate")
    if route.primary_route == "plan_create":
        if facts.has_saved_plan or not facts.plan_schema_valid:
            return decision("deny", "plan_create_schema_or_state_unverified")
        if not facts.effect_authorized:
            return decision("deny", "plan_write_authority_missing")
        if len(route.plan_step_evidence) < 2:
            return decision("deny", "plan_steps_source_unverified")
        return decision("candidate_execute", "requires_plan_create_cas")
    if route.primary_route in {"plan_revise", "plan_execute_first", "plan_resume"}:
        if not facts.has_saved_plan or facts.checkpoint_revision is None:
            return decision("deny", "missing_plan_checkpoint")
        if not facts.checkpoint_binding_verified or not facts.plan_schema_valid:
            return decision("deny", "plan_binding_unverified")
        if not facts.plan_step_states:
            return decision("deny", "plan_states_unverified")
        if route.primary_route == "plan_revise":
            if not facts.effect_authorized:
                return decision("deny", "revision_write_authority_missing")
            return decision("candidate_execute", "requires_plan_revision_cas")
        if route.primary_route == "plan_execute_first":
            if facts.plan_step_states[0] != "pending":
                return decision("deny", "first_step_not_pending")
        elif all(state == "completed" for state in facts.plan_step_states):
            return decision("noop", "plan_already_completed")
        elif any(state not in {"pending", "completed"} for state in facts.plan_step_states):
            return decision("deny", "invalid_plan_step_state")
        if not facts.tool_registered or not facts.tool_arguments_valid:
            return decision("deny", "plan_tool_binding_unverified")
        if route.effect_intent == "mutation_requested" and not facts.effect_authorized:
            return decision("deny", "plan_tool_write_authority_missing")
        return decision("candidate_execute", "requires_plan_step_cas")
    return decision("deny", "unknown_route")
