"""Pure M5 binding of a proposal to an M3 captured Turn; no execution rights."""
from dataclasses import dataclass
from typing import Literal
from agent.m3_turn_capture import compare_captured_identity, compare_checkpoint_snapshot
from agent.route_primary_m5 import PrimarySemanticProposal
from agent.route_primary_m5_binding import bind_primary_proposal, ProposalBindingRefused

@dataclass(frozen=True)
class CapturedProposal:
    turn_id: str
    request_id: str
    checkpoint_digest: str | None
    route: str
    scope: str
    execution_authorized: Literal[False] = False

def bind_captured_proposal(proposal: PrimarySemanticProposal, message: str,
                           capture, *, conversation_id: str, request_id: str,
                           turn_id: str, checkpoint: dict | None) -> CapturedProposal:
    if compare_captured_identity(capture, conversation_id=conversation_id,
                                 request_id=request_id, turn_id=turn_id)["result"] != "MATCH":
        raise ProposalBindingRefused("m5_turn_identity_mismatch")
    if compare_checkpoint_snapshot(capture, checkpoint)["result"] != "SAME":
        raise ProposalBindingRefused("m5_checkpoint_changed")
    if proposal.route in {"plan_execute_first", "plan_resume", "plan_revise", "plan_cancel"}:
        if not capture.checkpoint_present or capture.checkpoint_revision is None:
            raise ProposalBindingRefused("m5_saved_plan_unverified")
        if proposal.reference.kind != "saved_plan":
            raise ProposalBindingRefused("m5_plan_reference_unverified")
    if proposal.route == "plan_create" and capture.checkpoint_present:
        raise ProposalBindingRefused("m5_existing_plan")
    if proposal.reference.kind == "pending_slot" and not capture.pending_origin_present:
        raise ProposalBindingRefused("m5_pending_origin_unverified")
    bound = bind_primary_proposal(proposal, message)
    return CapturedProposal(turn_id, request_id, capture.checkpoint_digest,
                            bound.route, bound.scope)
