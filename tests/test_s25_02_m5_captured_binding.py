"""M5 semantic binding is identity- and preturn-checkpoint-bound."""
import pytest
from agent.m3_turn_capture import capture_turn
from agent.route_primary_m5 import PrimarySemanticProposal
from agent.route_primary_m5_binding import ProposalBindingRefused
from agent.route_primary_m5_capture import bind_captured_proposal


def make_proposal():
    return PrimarySemanticProposal.model_validate({
        "schema_version": "s25-m5-primary-v1",
        "route": "plan_execute_first", "scope": "first_step",
        "effect_intent": "read_only", "ambiguity": "clear",
        "reference": {"kind": "saved_plan"},
        "operation": {"route": "plan_execute_first", "scope": "first_step",
                      "evidence": [{"start": 0, "end": 3, "exact_text": "第一步"}]},
    })


def test_captured_binding_and_identity_fail_closed():
    plan = {"revision": 1, "steps": [{"status": "pending"}]}
    capture = capture_turn(conversation_id="c1", request_id="req1",
                           turn_id="turn1", checkpoint=plan,
                           pending_action=None, clarification_id=None,
                           pending_question=None)
    args = dict(conversation_id="c1", request_id="req1", turn_id="turn1",
                checkpoint=plan)
    proposal = make_proposal()
    result = bind_captured_proposal(proposal, "第一步执行", capture, **args)
    assert result.execution_authorized is False
    assert result.route == "plan_execute_first"
    with pytest.raises(ProposalBindingRefused, match="m5_turn_identity_mismatch"):
        bind_captured_proposal(proposal, "第一步执行", capture,
                               **(args | {"turn_id": "other"}))
    with pytest.raises(ProposalBindingRefused, match="m5_checkpoint_changed"):
        bind_captured_proposal(proposal, "第一步执行", capture,
                               **(args | {"checkpoint": {"revision": 2, "steps": []}}))


def test_captured_binding_rejects_unverified_plan_and_reference():
    capture = capture_turn(conversation_id="c1", request_id="req1",
                           turn_id="turn1", checkpoint=None, pending_action=None,
                           clarification_id=None, pending_question=None)
    with pytest.raises(ProposalBindingRefused, match="m5_saved_plan_unverified"):
        bind_captured_proposal(make_proposal(), "第一步执行", capture,
                               conversation_id="c1", request_id="req1",
                               turn_id="turn1", checkpoint=None)
    plan = {"revision": 1, "steps": [{"status": "pending"}]}
    other = capture_turn(conversation_id="c1", request_id="req1",
                         turn_id="turn1", checkpoint=plan, pending_action=None,
                         clarification_id=None, pending_question=None)
    wrong = make_proposal().model_copy(update={"reference": make_proposal().reference.model_copy(update={"kind": "none"})})
    with pytest.raises(ProposalBindingRefused, match="m5_plan_reference_unverified"):
        bind_captured_proposal(wrong, "第一步执行", other, conversation_id="c1",
                               request_id="req1", turn_id="turn1", checkpoint=plan)
