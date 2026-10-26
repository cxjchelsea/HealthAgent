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
    tool_schema_valid: bool = False
    plan_schema_valid: bool = False
    pending_original_request_bound: bool = False
    source_provenance_valid: bool = False

@dataclass(frozen=True)
class ShadowAdmission:
    route: str
    disposition: Literal["deny", "needs_evidence", "safe_non_effect", "potentially_admissible"]
    reason: str
    admitted_route: str | None = None
    evidence_complete: bool = False


def facts_from_legacy_observation(*, checkpoint: dict | None,
                                  pending_action: str | None,
                                  pending_field: str | None,
                                  pending_original_request_bound: bool,
                                  execution_mode: str | None,
                                  result: dict,
                                  registered_tools: set[str]) -> ShadowHostFacts:
    """Reduce trusted legacy state/outcome to bounded booleans for M2 only.

    This runs after finalization and cannot authorize the completed turn.  It
    deliberately ignores answer wording and model-generated arguments.
    """
    steps = (checkpoint or {}).get("steps", [])
    plan_states = tuple(
        step.get("status") for step in steps
        if isinstance(step, dict) and isinstance(step.get("status"), str))
    revision_valid = (isinstance((checkpoint or {}).get("revision"), int)
                      and bool(plan_states))
    trace = result.get("trace", []) if isinstance(result, dict) else []
    observed_tools = {
        item.get("name") for item in trace
        if isinstance(item, dict) and item.get("type") in {
            "tool", "clarification_resumed"
        } and isinstance(item.get("name"), str)
    }
    plan_tools = {
        item.get("expected_tool") for item in steps
        if isinstance(item, dict) and isinstance(item.get("expected_tool"), str)
    }
    relevant_tools = observed_tools or plan_tools
    tool_registered = bool(relevant_tools) and relevant_tools <= registered_tools
    reason = result.get("termination_reason") if isinstance(result, dict) else None
    plan_routes = {
        "plan_created_pending_execution", "plan_revised_pending_execution",
        "plan_first_step_verified", "plan_first_step_already_completed",
        "plan_already_completed",
    }
    has_plan_execution = any(
        isinstance(item, dict) and item.get("type") == "plan_resume_execution"
        for item in trace)
    bound_step_valid = bool(revision_valid and (
        reason in plan_routes or has_plan_execution))
    source_gate_passed = bool(
        execution_mode in {"read_only", "execute"}
        or observed_tools or reason in plan_routes)
    current_write = bool(
        execution_mode == "execute"
        or reason in {"plan_created_pending_execution",
                      "plan_revised_pending_execution"}
        or "create_task" in observed_tools)
    current_read = bool(
        execution_mode == "read_only" or observed_tools or has_plan_execution)
    plan_schema_valid = bool(
        result.get("plan_checkpoint") and result.get("plan")
        and all(isinstance(item, dict) for item in result.get("plan", [])))
    return ShadowHostFacts(
        has_plan=checkpoint is not None,
        plan_states=plan_states,
        revision_valid=revision_valid,
        bound_step_valid=bound_step_valid,
        pending_action=pending_action,
        pending_field=pending_field,
        current_write_authorized=current_write,
        current_read_bound=current_read,
        tool_registered=tool_registered,
        tool_schema_valid=tool_registered,
        plan_schema_valid=plan_schema_valid,
        pending_original_request_bound=pending_original_request_bound,
        source_provenance_valid=source_gate_passed,
    )

def preview_admission(proposal: ShadowRoute, facts: ShadowHostFacts) -> ShadowAdmission:
    """Pure design-matrix preview. potentially_admissible is NOT authorization."""
    route = proposal.primary_route
    def out(status: str, reason: str, admitted_route: str | None = None,
            evidence_complete: bool = False) -> ShadowAdmission:
        return ShadowAdmission(route, status, reason, admitted_route,
                               evidence_complete)
    if proposal.ambiguity != "clear":
        return out("deny", "ambiguous_route")
    if route == "clarify":
        return out("safe_non_effect", "clarification_only", "clarify", True)
    if route == "respond":
        return out("safe_non_effect", "no_effect_route", "respond", True)
    if route == "cancel":
        return out("safe_non_effect", "no_effect_route",
                   "cancel_no_side_effect", True)
    if route == "plan_create":
        # A model's plan label is not proof of a multi-step plan.
        spans = proposal.plan_step_evidence
        ordered = sorted(spans, key=lambda span: (span.start, span.end))
        if len(spans) < 2 or any(
                a.end > z.start for a, z in zip(ordered, ordered[1:])):
            return out("needs_evidence", "plan_steps_source_unverified")
        if facts.has_plan:
            return out("deny", "existing_saved_plan")
        if not facts.current_write_authorized:
            return out("needs_evidence", "plan_creation_source_unverified")
        if not facts.source_provenance_valid:
            return out("needs_evidence", "plan_creation_provenance_unverified")
        if not facts.plan_schema_valid:
            return out("needs_evidence", "plan_schema_unverified")
        return out("potentially_admissible", "shadow_evidence_complete",
                   "create_pending_plan", True)
    if route in {"plan_execute_first", "plan_resume", "plan_revise"}:
        if not facts.has_plan:
            return out("deny", "missing_saved_plan")
        if not facts.revision_valid:
            return out("needs_evidence", "checkpoint_revision_unverified")
        if route == "plan_revise":
            if (proposal.reference.kind != "saved_step"
                    or proposal.reference.step_number is None):
                return out("needs_evidence", "revision_step_reference_unverified")
            if not facts.bound_step_valid:
                return out("needs_evidence", "revision_binding_unverified")
            if not facts.current_write_authorized:
                return out("needs_evidence", "revision_user_source_unverified")
            if not facts.source_provenance_valid:
                return out("needs_evidence", "revision_provenance_unverified")
            return out("potentially_admissible", "shadow_evidence_complete",
                       "revise_pending_plan", True)
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
        if not facts.source_provenance_valid:
            return out("needs_evidence", "plan_execution_provenance_unverified")
        if not facts.tool_registered:
            return out("needs_evidence", "plan_tool_registration_unverified")
        if not facts.tool_schema_valid:
            return out("needs_evidence", "plan_tool_schema_unverified")
        admitted = ("execute_saved_first" if route == "plan_execute_first"
                    else "execute_saved_remaining")
        return out("potentially_admissible", "shadow_evidence_complete",
                   admitted, True)
    if route == "agent_loop":
        if proposal.route_target == "pending_clarification":
            if not facts.pending_action or not facts.pending_field:
                return out("deny", "missing_trusted_pending_binding")
            # The semantic producer may abstain from naming a Host-owned field.
            # A conflicting non-null field is denied; an omitted field is safely
            # resolved only from the single trusted pending binding below.
            if (proposal.reference.pending_field is not None
                    and proposal.reference.pending_field != facts.pending_field):
                return out("deny", "pending_field_mismatch")
            if not facts.pending_original_request_bound:
                return out("needs_evidence", "pending_original_request_authority_required")
            if not facts.source_provenance_valid:
                return out("needs_evidence", "pending_reply_provenance_unverified")
            if not facts.tool_registered:
                return out("needs_evidence", "tool_registration_unverified")
            if not facts.tool_schema_valid:
                return out("needs_evidence", "tool_schema_unverified")
            if proposal.effect_intent == "none":
                return out("needs_evidence", "pending_effect_unspecified")
            if proposal.reference.kind != "pending_slot":
                return out("needs_evidence", "pending_reference_unverified")
            return out("potentially_admissible", "shadow_evidence_complete",
                       "ordinary_loop", True)
        if (proposal.route_target == "none"
                and proposal.effect_intent != "none"):
            return out("deny", "effectful_loop_missing_tool_target")
        if proposal.route_target == "tool_action" and proposal.effect_intent == "none":
            return out("needs_evidence", "tool_effect_unspecified")
        if proposal.effect_intent == "none":
            return out("safe_non_effect", "text_only_loop", "ordinary_loop", True)
        if not facts.tool_registered:
            return out("needs_evidence", "tool_registration_unverified")
        if proposal.effect_intent == "mutation_requested":
            if not facts.current_write_authorized:
                return out("needs_evidence", "write_source_unverified")
            if not facts.source_provenance_valid:
                return out("needs_evidence", "write_provenance_unverified")
            if not facts.tool_schema_valid:
                return out("needs_evidence", "tool_schema_unverified")
            return out("potentially_admissible", "shadow_evidence_complete",
                       "ordinary_loop", True)
        if not facts.current_read_bound:
            return out("needs_evidence", "read_tool_argument_unverified")
        if not facts.source_provenance_valid:
            return out("needs_evidence", "read_provenance_unverified")
        if not facts.tool_schema_valid:
            return out("needs_evidence", "tool_schema_unverified")
        return out("potentially_admissible", "shadow_evidence_complete",
                   "ordinary_loop", True)
    return out("deny", "unknown_route")
