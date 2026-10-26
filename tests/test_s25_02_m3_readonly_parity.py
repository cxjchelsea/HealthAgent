"""M3-B comparator cannot execute or infer admission from legacy completion."""
from dataclasses import replace

import pytest

from agent.route_admission_m3 import PreTurnFacts
from agent.route_shadow import ShadowRoute
from evals.s25_02_m3_readonly_parity import ParityObservation, compare_readonly


def proposal(kind="agent_loop", **fields):
    data = dict(schema_version="s25-route-v2", primary_route=kind,
                route_target="tool_action", execution_scope="single_tool",
                effect_intent="read_only", ambiguity="clear")
    data.update(fields)
    return ShadowRoute.model_validate(data)


@pytest.fixture
def trusted():
    return PreTurnFacts(
        conversation_id="c",request_id="r",request_is_fresh=True,
        session_lock_held=True,idempotency_authority_verified=True,
        user_source_verified=True,tool_registered=True,tool_arguments_valid=True)


def test_matching_tool_route_is_only_candidate(trusted):
    out = compare_readonly(proposal(),trusted,ParityObservation("agent_loop", True))
    assert out["result"] == "COMPATIBLE"
    assert out["execution_authorized"] is False


def test_detects_wrong_task_as_plan(trusted):
    out=compare_readonly(proposal("plan_create",route_target="none",
        execution_scope="none",effect_intent="mutation_requested"),
        trusted,ParityObservation("agent_loop", True))
    assert out["result"]=="MISMATCH"
    assert out["reason"]=="route_disagreement"


def test_missing_registration_is_not_excused_by_legacy_success(trusted):
    out=compare_readonly(proposal(),replace(trusted,tool_registered=False),
                         ParityObservation("agent_loop",True))
    assert out["result"]=="MISMATCH"
    assert out["reason"]=="effect_without_m3_evidence"


def test_missing_legacy_evidence_abstains(trusted):
    out=compare_readonly(proposal(),trusted,ParityObservation(None,False))
    assert out["result"]=="INSUFFICIENT"


def test_untrusted_evidence_kind_abstains(trusted):
    out=compare_readonly(proposal(),trusted,
                         ParityObservation("agent_loop",True,"model_assertion"))
    assert out["result"]=="INSUFFICIENT"


def test_clarification_non_effect_is_compatible(trusted):
    route=proposal("clarify",route_target="none",execution_scope="none",
        effect_intent="none",clarification={
            "required":True,"reason":"missing_argument","question":"哪个参数？"})
    out=compare_readonly(route,trusted,ParityObservation("clarify",False))
    assert out["result"]=="COMPATIBLE"
    assert out["execution_authorized"] is False


def test_legacy_effect_on_clarification_is_mismatch(trusted):
    route=proposal("clarify",route_target="none",execution_scope="none",
        effect_intent="none",clarification={
            "required":True,"reason":"missing_argument","question":"哪个参数？"})
    out=compare_readonly(route,trusted,ParityObservation("clarify",True))
    assert out["result"]=="MISMATCH"
