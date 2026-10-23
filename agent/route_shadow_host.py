"""M2-only deterministic hypothetical route admission.

Never authorizes or executes operations. Missing trusted proof is a denial,
not permission inferred from model words. No persistence or external access.
"""
from dataclasses import dataclass
from typing import Literal

from agent.route_shadow import ShadowRoute

@dataclass(frozen=True)
class ShadowHostFacts:
    has_plan: bool
    plan_states: tuple[str, ...] = ()
    revision_valid: bool = False
    bound_step_valid: bool = False
    pending_action: str | None = None
    pending_field: str | None = None
    current_write_authorized: bool = False
    current_read_bound: bool = False
    tool_registered: bool = False

@dataclass(frozen=True)
class ShadowAdmission:
    route: str
    disposition: Literal["deny", "needs_evidence", "safe_non_effect", "potentially_admissible"]
    reason: str

def preview_admission(proposal: ShadowRoute, facts: ShadowHostFacts) -> ShadowAdmission:
    """Pure design-matrix preview. potentially_admissible is NOT authorization."""
    route = proposal.primary_route
    def out(status: str, reason: str) -> ShadowAdmission:
        return ShadowAdmission(route, status, reason)
    if proposal.ambiguity != "clear":
        return out("deny", "ambiguous_route")
    if route == "clarify":
        return out("safe_non_effect", "clarification_only")
    if route in {"respond", "cancel"}:
        return out("safe_non_effect", "no_effect_route")
    if route == "plan_create":
        if facts.has_plan:
            return out("deny", "existing_saved_plan")
        if not facts.current_write_authorized:
            return out("needs_evidence", "plan_creation_source_unverified")
        return out("potentially_admissible", "requires_plan_schema_verification")
    if route in {"plan_execute_first", "plan_resume", "plan_revise"}:
        if not facts.has_plan:
            return out("deny", "missing_saved_plan")
        if not facts.revision_valid:
            return out("needs_evidence", "checkpoint_revision_unverified")
        if route == "plan_revise":
            if not facts.bound_step_valid:
                return out("needs_evidence", "revision_binding_unverified")
            if not facts.current_write_authorized:
                return out("needs_evidence", "revision_user_source_unverified")
            return out("potentially_admissible", "requires_plan_transition_verification")
        if not facts.plan_states:
            return out("needs_evidence", "plan_steps_unverified")
        if route == "plan_execute_first":
            if facts.plan_states[0] == "completed":
                return out("safe_non_effect", "first_step_already_completed")
            if facts.plan_states[0] != "pending":
                return out("deny", "invalid_first_step_state")
        elif all(s == "completed" for s in facts.plan_states):
            return out("safe_non_effect", "plan_already_completed")
        elif any(s not in {"pending", "completed"} for s in facts.plan_states):
            return out("deny", "invalid_plan_state")
        if not facts.bound_step_valid:
            return out("needs_evidence", "saved_step_binding_unverified")
        if not facts.current_read_bound:
            return out("needs_evidence", "plan_execution_user_source_unverified")
        return out("potentially_admissible", "requires_tool_permission_verification")
    if route == "agent_loop":
        if proposal.route_target == "pending_clarification":
            if not facts.pending_action or not facts.pending_field:
                return out("deny", "missing_trusted_pending_binding")
            if proposal.reference.pending_field != facts.pending_field:
                return out("deny", "pending_field_mismatch")
            return out("needs_evidence", "pending_original_request_authority_required")
        if proposal.effect_intent == "none":
            return out("safe_non_effect", "text_only_loop")
        if not facts.tool_registered:
            return out("needs_evidence", "tool_registration_unverified")
        if proposal.effect_intent == "mutation_requested":
            if not facts.current_write_authorized:
                return out("needs_evidence", "write_source_unverified")
            return out("potentially_admissible", "requires_tool_schema_verification")
        if not facts.current_read_bound:
            return out("needs_evidence", "read_tool_argument_unverified")
        return out("potentially_admissible", "requires_tool_schema_verification")
    return out("deny", "unknown_route")
