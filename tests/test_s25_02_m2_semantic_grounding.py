"""M2 semantic grounding never silently rewrites the model route or effect."""
import asyncio
import json

import pytest

from agent.route_shadow import ShadowRoute, evaluate_shadow, validate_semantic_grounding


def proposal(kind, target, scope, effect, **extra):
    return ShadowRoute.model_validate({
        "schema_version": "s25-route-v2", "primary_route": kind,
        "route_target": target, "execution_scope": scope,
        "effect_intent": effect, "ambiguity": "clear", **extra})


@pytest.mark.parametrize("kind,target,scope,effect,expected", [
    ("agent_loop", "pending_clarification", "single_tool", "none",
     "shadow_tool_effect_unspecified"),
    ("agent_loop", "tool_action", "single_tool", "none",
     "shadow_tool_effect_unspecified"),
    ("plan_create", "saved_plan", "none", "mutation_requested",
     "shadow_plan_steps_ungrounded"),
    ("plan_revise", "saved_plan", "none", "mutation_requested",
     "shadow_revision_step_unverified"),
])
def test_missing_semantic_proof_fails_closed(kind,target,scope,effect,expected):
    with pytest.raises(ValueError, match=expected):
        validate_semantic_grounding(proposal(kind,target,scope,effect),"新请求")


def test_valid_pending_reply_preserves_readonly_effect():
    p = proposal("agent_loop","pending_clarification","single_tool","read_only",
                 reference={"kind":"pending_slot", "pending_field":"term"})
    validate_semantic_grounding(p,"RAG")
    assert p.primary_route == "agent_loop"
    assert p.effect_intent == "read_only"


def test_nonoverlapping_plan_steps_are_accepted():
    message = "先查询 RAG，再计算 8*8"
    def span(fragment):
        start=message.index(fragment)
        return {"start":start,"end":start+len(fragment),"exact_text":fragment}
    p=proposal("plan_create","none","none","mutation_requested",
               plan_step_evidence=[span("查询 RAG"),span("计算 8*8")])
    validate_semantic_grounding(p,message)


def test_reference_pending_slot_must_be_typed():
    p=proposal("agent_loop","pending_clarification","single_tool","read_only")
    with pytest.raises(ValueError, match="shadow_pending_reference_unverified"):
        validate_semantic_grounding(p,"RAG")


def test_evaluator_never_promotes_bad_pending_effect_to_readonly():
    p=proposal("agent_loop","pending_clarification","single_tool","none",
               reference={"kind":"pending_slot","pending_field":"term"})
    class Stub:
        async def complete(self, **kwargs):
            return p.model_dump_json()
    output=asyncio.run(evaluate_shadow(
        Stub(),"stub","RAG",has_plan=False,pending_action="mcp_reference_lookup"))
    assert output["state"]=="unavailable"
    assert output["validation_code"]=="shadow_tool_effect_unspecified"
    assert "read_only" not in json.dumps(output)
