"""M3-B exact-identity, no-side-effect evidence comparator.

Consumes externally supplied pre-turn facts and legacy result from one Turn.
Never reconstructs pre-turn authorization from post-turn legacy outcomes.
"""
from dataclasses import dataclass

from agent.route_admission_m3 import PreTurnFacts
from agent.route_shadow import ShadowRoute
from evals.s25_02_m3_readonly_parity import ParityObservation, compare_readonly


@dataclass(frozen=True)
class TurnIdentity:
    conversation_id: str
    request_id: str
    turn_id: str


@dataclass(frozen=True)
class ExactTurnEvidence:
    identity: TurnIdentity
    legacy_route: str | None
    legacy_effect_occurred: bool
    preturn_identity: TurnIdentity | None = None
    final_identity: TurnIdentity | None = None
    preturn_capture_origin: str = "unverified"


def compare_exact_turn(
    proposal: ShadowRoute, facts: PreTurnFacts, evidence: ExactTurnEvidence,
) -> dict[str, str | bool]:
    def insufficient(reason: str) -> dict[str, str | bool]:
        return {"result": "INSUFFICIENT", "reason": reason,
                "execution_authorized": False}
    expected = evidence.identity
    if not all((expected.conversation_id, expected.request_id, expected.turn_id)):
        return insufficient("incomplete_turn_identity")
    if (facts.conversation_id, facts.request_id) != (
            expected.conversation_id, expected.request_id):
        return insufficient("preturn_request_identity_mismatch")
    if evidence.preturn_capture_origin != "pre_execution_host_snapshot":
        return insufficient("preturn_snapshot_not_verified")
    if evidence.preturn_identity != expected:
        return insufficient("preturn_turn_binding_mismatch")
    if evidence.final_identity != expected:
        return insufficient("final_turn_binding_mismatch")
    return compare_readonly(proposal, facts, ParityObservation(
        evidence.legacy_route, evidence.legacy_effect_occurred, "observed_legacy"))
