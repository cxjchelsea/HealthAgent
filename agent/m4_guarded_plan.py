"""Bounded M4 guarded admission for creating (not executing) saved plans."""
from agent.route_admission_m3 import PreTurnFacts, decide_admission
from agent.route_shadow import EvidenceSpan, ShadowRoute


def admit_guarded_plan_create(*, message: str, conversation_id: str,
                              request_id: str, checkpoint: dict,
                              turn_created: bool,
                              semantic_confirmed: bool) -> bool:
    """A bounded proposal only; original PlanStore CAS is the final authority.

    The model's bindings must already have passed build_plan_checkpoint.
    Additionally locate two distinct, exact current-message step bindings.
    """
    if not (turn_created and semantic_confirmed and isinstance(checkpoint, dict)):
        return False
    steps = checkpoint.get("steps", [])
    if not (isinstance(steps, list) and 2 <= len(steps) <= 8
            and checkpoint.get("revision") == 1):
        return False
    spans = []
    for step in steps:
        if (not isinstance(step, dict) or step.get("status") != "pending"
                or not isinstance(step.get("expected_arguments"), dict)):
            return False
        args = step["expected_arguments"]
        source = args.get("expression", args.get("term"))
        if not isinstance(source, str) or not source:
            return False
        first = message.find(source)
        if first < 0 or message.find(source, first + 1) >= 0:
            return False
        spans.append(EvidenceSpan(
            start=first, end=first + len(source), exact_text=source))
    ordered = sorted(spans, key=lambda item: item.start)
    if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
        return False
    route = ShadowRoute.model_validate({
        "schema_version": "s25-route-v2", "primary_route": "plan_create",
        "route_target": "none", "execution_scope": "none",
        "effect_intent": "mutation_requested", "ambiguity": "clear",
        "plan_step_evidence": [s.model_dump() for s in ordered],
    })
    facts = PreTurnFacts(
        conversation_id=conversation_id, request_id=request_id,
        has_saved_plan=False, user_source_verified=True,
        effect_authorized=True, plan_schema_valid=True,
        request_is_fresh=turn_created, session_lock_held=turn_created,
        idempotency_authority_verified=turn_created)
    return decide_admission(route, facts).disposition == "candidate_execute"
