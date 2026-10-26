"""M3-B exact-identity guard regression, no external state."""
from dataclasses import replace

from agent.route_admission_m3 import PreTurnFacts
from agent.route_shadow import ShadowRoute
from evals.s25_02_m3_identity_parity import (
    ExactTurnEvidence, TurnIdentity, compare_exact_turn,
)


def _proposal():
    return ShadowRoute.model_validate(dict(
        schema_version="s25-route-v2", primary_route="agent_loop",
        route_target="tool_action", execution_scope="single_tool",
        effect_intent="read_only", ambiguity="clear"))


def _facts():
    return PreTurnFacts(
        conversation_id="c",request_id="r", request_is_fresh=True,
        session_lock_held=True,idempotency_authority_verified=True,
        user_source_verified=True,tool_registered=True,tool_arguments_valid=True)


def _evidence(**changes):
    identity=TurnIdentity("c","r","t")
    baseline=ExactTurnEvidence(
        identity, "agent_loop", True, identity, identity,
        "pre_execution_host_snapshot")
    return replace(baseline,**changes)


def test_postturn_reconstruction_is_not_valid_preturn_evidence():
    verdict=compare_exact_turn(_proposal(),_facts(),_evidence(
        preturn_capture_origin="reconstructed_from_legacy"))
    assert verdict["result"]=="INSUFFICIENT"
    assert verdict["reason"]=="preturn_snapshot_not_verified"


def test_exact_identity_mismatch_rejected():
    verdict=compare_exact_turn(_proposal(),_facts(),_evidence(
        preturn_identity=TurnIdentity("c","r","other")))
    assert verdict["result"]=="INSUFFICIENT"


def test_request_mismatch_rejected():
    verdict=compare_exact_turn(_proposal(),replace(_facts(),request_id="different"),
                               _evidence())
    assert verdict["reason"]=="preturn_request_identity_mismatch"


def test_missing_final_turn_binding_rejected():
    verdict=compare_exact_turn(_proposal(),_facts(),_evidence(final_identity=None))
    assert verdict["reason"]=="final_turn_binding_mismatch"


def test_exact_fixture_is_compatible_but_never_authority():
    verdict=compare_exact_turn(_proposal(),_facts(),_evidence())
    assert verdict["result"]=="COMPATIBLE"
    assert verdict["execution_authorized"] is False


def test_legacy_route_disagreement_detected():
    verdict=compare_exact_turn(_proposal(),_facts(),_evidence(legacy_route="plan_create"))
    assert verdict["result"]=="MISMATCH"
