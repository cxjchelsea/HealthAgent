"""Read-only verification of a separately supplied ShadowRoute evidence proposal.

The frozen core classifier supplies route identity. Evidence is never promoted
to authority, and the model proposal may not override core intent.
"""
from dataclasses import dataclass
from typing import Literal

from agent.route_semantic_hierarchical import HierarchicalDiagnostic
from agent.route_semantic_preview import ROUTE_BY_CORE
from agent.route_shadow import (
    ShadowRoute, bind_unique_quotes, bind_explicit_revision_step,
    bind_diagnostic_pending_reference, verify_source_spans,
    verify_trusted_reference, validate_semantic_grounding,
)
from agent.route_shadow_host import ShadowHostFacts, preview_admission


@dataclass(frozen=True)
class VerifiedCorePreview:
    candidate_route: str | None
    disposition: Literal["deny", "needs_evidence", "safe_non_effect",
                         "potentially_admissible"]
    reason: str
    host_authorization: Literal[False] = False


def verify_core_evidence(
    core: HierarchicalDiagnostic, proposal: ShadowRoute, message: str,
    facts: ShadowHostFacts,
) -> VerifiedCorePreview:
    """Fail closed; only trusted facts and literal user evidence can be bound."""
    if core.failure_stage or core.object_type is None or core.operation is None:
        return VerifiedCorePreview(None, "deny", "semantic_stage_failure")
    expected_route = ROUTE_BY_CORE.get((core.object_type, core.operation))
    if expected_route is None:
        return VerifiedCorePreview(None, "deny", "invalid_core_pair")
    if proposal.primary_route != expected_route:
        return VerifiedCorePreview(expected_route, "deny", "core_route_conflict")

    # The core alone cannot choose effect type, target, or tool arguments.
    # In particular, both task/create and tool/invoke compile to agent_loop.
    if core.object_type == "pending_slot" and core.operation == "invoke":
        if proposal.route_target != "pending_clarification":
            return VerifiedCorePreview(expected_route, "deny", "core_target_conflict")
    if core.object_type == "tool" and proposal.route_target != "tool_action":
        return VerifiedCorePreview(expected_route, "deny", "core_target_conflict")
    if core.object_type == "task" and proposal.route_target != "tool_action":
        return VerifiedCorePreview(expected_route, "deny", "core_target_conflict")
    if core.object_type == "pending_slot" and core.operation == "cancel":
        if proposal.route_target != "pending_clarification":
            return VerifiedCorePreview(expected_route, "deny", "core_target_conflict")
    if core.object_type == "plan" and core.operation == "cancel":
        if proposal.route_target != "saved_plan":
            return VerifiedCorePreview(expected_route, "deny", "core_target_conflict")

    if expected_route in {"plan_execute_first", "plan_resume", "plan_revise"}:
        if not facts.has_plan:
            return VerifiedCorePreview(expected_route, "deny", "missing_saved_plan")
    if proposal.ambiguity != "clear":
        return VerifiedCorePreview(expected_route, "deny", "ambiguous_route")

    try:
        bound = bind_unique_quotes(proposal, message)
        verify_source_spans(bound, message)
        if expected_route == "plan_revise":
            bound = bind_explicit_revision_step(bound, message, facts)
        if core.object_type == "pending_slot" and core.operation == "invoke":
            bound = bind_diagnostic_pending_reference(
                bound, pending_action=facts.pending_action,
                pending_field=facts.pending_field, host_facts=facts)
        verify_trusted_reference(
            bound, pending_field=facts.pending_field, host_facts=facts)
        validate_semantic_grounding(bound, message)
    except ValueError:
        return VerifiedCorePreview(expected_route, "deny", "reference_or_source_invalid")

    # No effectful preview may proceed from fabricated or absent user quotes.
    if expected_route not in {"respond", "clarify"} and not bound.source_evidence:
        return VerifiedCorePreview(expected_route, "needs_evidence",
                                   "current_turn_source_unverified")
    if expected_route == "plan_revise" and not bound.plan_step_evidence:
        return VerifiedCorePreview(expected_route, "needs_evidence",
                                   "revision_step_source_unverified")
    admission = preview_admission(bound, facts)
    return VerifiedCorePreview(expected_route, admission.disposition,
                               admission.reason)
