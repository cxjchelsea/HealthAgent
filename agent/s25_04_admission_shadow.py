"""Non-authoritative S25-04 Host admission shadow. Never grants execution."""
from dataclasses import dataclass
from typing import Literal

Advisory = Literal["not_eligible", "review_candidate"]


@dataclass(frozen=True)
class AdmissionShadow:
    advisory: Advisory
    reason: str
    execution_authorized: bool = False


def evaluate_admission_shadow(
    *,
    classification: str,
    classifier_reason: str,
    request: str,
    evidence_quote: str | None,
    preturn_plan_present: bool | None,
    checkpoint_trusted: bool,
    mixed_or_plan_intent_excluded: bool = False,
) -> AdmissionShadow:
    """Review-only; independent exclusion must be Host-verified, never model asserted."""
    def deny(reason: str) -> AdmissionShadow:
        return AdmissionShadow("not_eligible", reason)

    if not checkpoint_trusted or preturn_plan_present is None:
        return deny("untrusted_checkpoint")
    if classification != "ordinary_tool" or classifier_reason != "bound":
        return deny("not_confident_ordinary")
    if not request or not evidence_quote or request.count(evidence_quote) != 1:
        return deny("unbound_quote")
    if not mixed_or_plan_intent_excluded:
        return deny("independent_exclusion_missing")
    return AdmissionShadow("review_candidate", "review_only")
