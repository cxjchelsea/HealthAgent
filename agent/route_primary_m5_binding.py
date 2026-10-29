"""M5 fail-closed, pure source binding (no permission or execution)."""
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from agent.route_primary_m5 import PrimarySemanticProposal
from agent.route_shadow import EvidenceSpan


@dataclass(frozen=True)
class BoundSemanticIntent:
    route: str
    scope: str
    effect_intent: str
    source_sha256: str
    proposal_sha256: str
    # A semantic binding is never an execution authorization.
    execution_authorized: Literal[False] = False


class ProposalBindingRefused(ValueError):
    pass


def bind_primary_proposal(proposal: PrimarySemanticProposal,
                          current_message: str) -> BoundSemanticIntent:
    """Validate all source spans; leave trusted checkpoint/permission to Host."""
    spans: list[EvidenceSpan] = list(proposal.evidence)
    operation = proposal.operation
    if hasattr(operation, "evidence"):
        spans.extend(operation.evidence)
    if hasattr(operation, "steps"):
        for step in operation.steps:
            spans.extend(step.evidence)
    if hasattr(operation, "candidate_tool") and operation.candidate_tool is not None:
        spans.extend(operation.candidate_tool.evidence)
    if proposal.route not in {"respond", "clarify"} and not spans:
        raise ProposalBindingRefused("m5_missing_current_turn_evidence")
    for span in spans:
        if current_message[span.start:span.end] != span.exact_text:
            raise ProposalBindingRefused("m5_source_span_mismatch")
    # Source verification does not certify permissions or linked plan existence.
    return BoundSemanticIntent(
        route=proposal.route, scope=proposal.scope,
        effect_intent=proposal.effect_intent,
        source_sha256=sha256(current_message.encode("utf-8")).hexdigest(),
        proposal_sha256=sha256(proposal.model_dump_json().encode("utf-8")).hexdigest(),
    )
