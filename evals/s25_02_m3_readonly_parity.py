"""M3-B: offline, read-only comparison of pre-turn candidate and legacy evidence.

Frozen fixtures are regression controls, NEVER proof of real-time parity.
"""
from dataclasses import dataclass

from agent.route_admission_m3 import PreTurnFacts, decide_admission
from agent.route_shadow import ShadowRoute


@dataclass(frozen=True)
class ParityObservation:
    legacy_route: str | None
    legacy_effect_occurred: bool
    legacy_evidence_kind: str = "frozen_fixture"


def compare_readonly(
    proposal: ShadowRoute,
    facts: PreTurnFacts,
    legacy: ParityObservation,
) -> dict[str, str | bool]:
    """No tool/DB calls; never promotes a model candidate to execution authority."""
    decision = decide_admission(proposal, facts)
    if legacy.legacy_evidence_kind not in {"frozen_fixture", "observed_legacy"}:
        return {"result": "INSUFFICIENT", "reason": "untrusted_legacy_evidence",
                "admission": decision.disposition, "execution_authorized": False}
    if not legacy.legacy_route:
        return {"result": "INSUFFICIENT", "reason": "missing_legacy_route",
                "admission": decision.disposition, "execution_authorized": False}
    if legacy.legacy_route != proposal.primary_route:
        return {"result": "MISMATCH", "reason": "route_disagreement",
                "admission": decision.disposition, "execution_authorized": False}
    if legacy.legacy_effect_occurred and decision.disposition in {"deny", "clarify", "noop"}:
        return {"result": "MISMATCH", "reason": "effect_without_m3_evidence",
                "admission": decision.disposition, "execution_authorized": False}
    if decision.disposition == "candidate_execute":
        return {"result": "COMPATIBLE", "reason": "candidate_only_not_authorized",
                "admission": decision.disposition, "execution_authorized": False}
    return {"result": "COMPATIBLE", "reason": "nonexecuting_route",
            "admission": decision.disposition, "execution_authorized": False}
