"""M4 bounded saved-plan execution admission.

A candidate is never execution authority; existing PlanStore claim and
AgentLoop tool guards remain the only dispatch boundary.
"""
from agent.plan_intent import READ_TOOLS
from agent.route_admission_m3 import PreTurnFacts, decide_admission
from agent.route_shadow import ShadowRoute
from agent.tools import SCHEMAS


def admit_guarded_plan_execution(*, conversation_id: str, request_id: str,
                                 checkpoint: dict | None, scope: str,
                                 turn_created: bool,
                                 semantic_confirmed: bool) -> bool:
    if not (turn_created and semantic_confirmed
            and isinstance(checkpoint, dict)
            and scope in {"execute_first", "resume"}):
        return False
    revision = checkpoint.get("revision")
    steps = checkpoint.get("steps")
    if (type(revision) is not int or revision < 1
            or not isinstance(steps, list) or not 1 <= len(steps) <= 8):
        return False
    if any(not isinstance(step, dict)
           or step.get("status") not in {"pending", "completed"}
           for step in steps):
        return False
    pending = ([steps[0]] if scope == "execute_first"
               else [step for step in steps if step["status"] == "pending"])
    if not pending or any(step["status"] != "pending" for step in pending):
        return False
    for step in pending:
        name, args = step.get("expected_tool"), step.get("expected_arguments")
        if name not in READ_TOOLS or name not in SCHEMAS or not isinstance(args, dict):
            return False
        try:
            if SCHEMAS[name].model_validate(args).model_dump() != args:
                return False
        except ValueError:
            return False
    route = ShadowRoute.model_validate({
        "schema_version": "s25-route-v2",
        "primary_route": "plan_execute_first" if scope == "execute_first" else "plan_resume",
        "route_target": "saved_plan",
        "execution_scope": "first_step" if scope == "execute_first" else "remaining_steps",
        "effect_intent": "read_only",
        "ambiguity": "clear",
        "reference": {"kind": "saved_plan"},
    })
    facts = PreTurnFacts(
        conversation_id=conversation_id, request_id=request_id,
        checkpoint_revision=revision, has_saved_plan=True,
        plan_step_states=tuple(step["status"] for step in steps),
        user_source_verified=True, effect_authorized=False,
        tool_registered=True, tool_arguments_valid=True,
        plan_schema_valid=True, checkpoint_binding_verified=True,
        request_is_fresh=turn_created, session_lock_held=turn_created,
        idempotency_authority_verified=turn_created)
    return decide_admission(route, facts).disposition == "candidate_execute"
