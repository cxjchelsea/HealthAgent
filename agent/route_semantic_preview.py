"""Diagnostic M2 core-to-route preview. Does not authorize, bind, or execute."""
from dataclasses import dataclass
from typing import Literal

from agent.route_semantic_hierarchical import HierarchicalDiagnostic
from agent.route_shadow import ShadowRoute
from agent.route_shadow_host import ShadowAdmission, ShadowHostFacts, preview_admission


ROUTE_BY_CORE = {
    ("none", "answer"): "respond",
    ("none", "clarify"): "clarify",
    ("task", "create"): "agent_loop",
    ("tool", "invoke"): "agent_loop",
    ("pending_slot", "invoke"): "agent_loop",
    ("pending_slot", "cancel"): "cancel",
    ("plan", "create"): "plan_create",
    ("plan", "execute_first"): "plan_execute_first",
    ("plan", "resume"): "plan_resume",
    ("plan", "cancel"): "cancel",
    ("plan_step", "revise"): "plan_revise",
}

@dataclass(frozen=True)
class CoreRoutePreview:
    candidate_route: str | None
    disposition: Literal["safe_non_effect", "needs_evidence", "deny"]
    reason: str
    host_authorization: Literal[False] = False


def compile_core_preview(
    core: HierarchicalDiagnostic, facts: ShadowHostFacts
) -> CoreRoutePreview:
    """Compile intent only. Never invent evidence, arguments, references or effects."""
    if core.failure_stage or core.object_type is None or core.operation is None:
        return CoreRoutePreview(None, "deny", "semantic_stage_failure")
    route = ROUTE_BY_CORE.get((core.object_type, core.operation))
    if route is None:
        return CoreRoutePreview(None, "deny", "invalid_core_pair")
    # Only plain conversational response has a complete effect-free route.
    # Cancel is deliberately NOT automatically safe: target binding is absent.
    if route == "respond":
        proposal = ShadowRoute(
            schema_version="s25-route-v2", primary_route="respond",
            route_target="none", execution_scope="none",
            effect_intent="none", ambiguity="clear",
        )
        admission: ShadowAdmission = preview_admission(proposal, facts)
        return CoreRoutePreview(route, "safe_non_effect", admission.reason)
    if route in {"plan_execute_first", "plan_resume", "plan_revise"} and not facts.has_plan:
        return CoreRoutePreview(route, "deny", "missing_saved_plan")
    if route == "agent_loop" and core.object_type == "pending_slot" and not (
        facts.pending_action and facts.pending_field
        and facts.pending_original_request_bound
    ):
        return CoreRoutePreview(route, "deny", "missing_trusted_pending_binding")
    return CoreRoutePreview(route, "needs_evidence", "route_evidence_unbound")
