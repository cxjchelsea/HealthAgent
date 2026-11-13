"""B3c integrated: atomic second Turn close after two-step physical history.

No actual tool calls or HTTP. Exact two-step revision2 chain only.
"""
import json
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import event

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, ConversationPlan,
    DependencyV2StepReservation, DependencyWorkerFence, Message,
    PlanExecutionClaim, PlanToolDispatch, PrimaryRouteEvidence, Turn,
)
from services.dependency_fencing import assert_dependency_fence_in_session
from services.dependency_revision_two_step_terminal import (
    finalize_second_revised_turn_in_session,
)
from test_s25_07_revision_second import (
    store, _prepare, _begin, _receipt, _checkpoint,
)


def _complete(Session, cid):
    prior, old_fence, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, prior, cfg, tid, decision, fence, raw)
    with Session() as db:
        first = db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, plan_revision=2, step_id=1).one()
        definition = json.loads(first.receipt_json)["definition"]
    _receipt(Session, prior, cfg, tid, fence, raw, did, {
        "ok": True, "characters": len(definition),
        "words": len(definition.split())})
    checkpoint = _checkpoint(Session, prior, cfg, tid, fence, raw, did)
    with Session() as db:
        evolved = db.get(ConversationPlan, cid).snapshot
        assert json.loads(evolved) == checkpoint
    return prior, old_fence, cfg, tid, fence, did, evolved


def _final(Session, prior, cfg, tid, fence, evolved):
    with Session.begin() as db:
        return finalize_second_revised_turn_in_session(
            db, settings=cfg, owner_id=prior.owner_id,
            conversation_id=prior.conversation_id,
            turn_id=tid, request_id="continuation-"+tid,
            expected_raw=evolved, fence=fence)


def test_v2_second_turn_completed_releases_claim_and_fences_worker(store):
    Session, cid = store
    prior, old, cfg, tid, fence, did, evolved = _complete(Session, cid)
    assert _final(Session, prior, cfg, tid, fence, evolved) == "completed"
    with Session() as db:
        turn = db.get(Turn, tid)
        assert turn.state == "completed" and turn.failure_code is None
        msg = db.get(Message, turn.assistant_message_id)
        assert msg.role == "assistant" and msg.status == "completed"
        assert turn.output_chars == len(msg.content)
        assert db.get(PlanExecutionClaim, cid) is None
        worker = db.get(DependencyWorkerFence, cid)
        assert worker.state == "fenced"
        assert worker.turn_id == tid
        assert worker.generation == old.generation + 1
        assert db.get(PlanToolDispatch, did).status == "checkpointed"
        assert db.get(AgentTaskState, cid).state == "completed"
        assert db.get(ConversationPlan, cid).snapshot == evolved
        assert db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, plan_revision=2).count() == 2
    with pytest.raises(DependencyPlanError):
        _final(Session, prior, cfg, tid, fence, evolved)
    with pytest.raises(DependencyPlanError):
        with Session.begin() as db:
            assert_dependency_fence_in_session(
                db, conversation_id=cid, turn_id=tid, fence=fence)


@pytest.mark.parametrize("tamper", [
    "gate_disabled", "stale_fence", "claim_snapshot", "saved_snapshot",
    "first_receipt", "second_receipt", "first_attempt", "second_attempt",
    "first_reservation", "second_reservation", "first_turn",
    "second_host", "task_state", "step_transition", "task_transition",
    "extra_dispatch", "wrong_owner",
])
def test_two_step_terminal_denies_corruption_without_releasing_claim(store, tamper):
    Session, cid = store
    prior, old, cfg, tid, fence, did, evolved = _complete(Session, cid)
    owner = prior
    if tamper == "gate_disabled":
        cfg = cfg.model_copy(update={
            "s25_07_revision_continuation_enabled": False})
    elif tamper == "stale_fence":
        fence = replace(fence, generation=fence.generation + 1)
    elif tamper == "wrong_owner":
        owner = replace(prior, owner_id="not-owner")
    else:
        with Session.begin() as db:
            first = db.query(PlanToolDispatch).filter_by(
                conversation_id=cid, plan_revision=2, step_id=1).one()
            if tamper == "claim_snapshot":
                db.get(PlanExecutionClaim, cid).snapshot = "{}"
            elif tamper == "saved_snapshot":
                db.get(ConversationPlan, cid).snapshot = "{}"
            elif tamper == "first_receipt":
                first.receipt_json = '{"ok":true,"definition":"forged"}'
            elif tamper == "second_receipt":
                db.get(PlanToolDispatch, did).receipt_json = '{"ok":true}'
            elif tamper == "first_attempt":
                db.get(AgentAttempt, first.id).run_id = "tampered"
            elif tamper == "second_attempt":
                db.get(AgentAttempt, did).result_projection = "{}"
            elif tamper == "first_reservation":
                db.get(DependencyV2StepReservation, (cid, first.step_id)).turn_id = tid
            elif tamper == "second_reservation":
                db.get(DependencyV2StepReservation, (cid, 2)).turn_id = first.turn_id
            elif tamper == "first_turn":
                db.get(Turn, first.turn_id).state = "completed"
            elif tamper == "second_host":
                db.get(PrimaryRouteEvidence, tid).approved_request_digest = "0"*64
            elif tamper == "task_state":
                db.get(AgentTaskState, cid).state = "partial"
            elif tamper == "step_transition":
                t = db.query(AgentStateTransition).filter_by(
                    conversation_id=cid, reason_code="verified_step_completed",
                    step_id=2).one()
                t.attempt_id = "fake"
            elif tamper == "task_transition":
                t = db.query(AgentStateTransition).filter_by(
                    conversation_id=cid, reason_code="task_status_changed",
                    to_state="completed").one()
                t.from_state = "planned"
            elif tamper == "extra_dispatch":
                db.add(PlanToolDispatch(
                    id=str(uuid.uuid4()), conversation_id=cid,
                    turn_id=tid, step_id=999, plan_revision=2,
                    tool_name="text_stats", tool_version="1.0.0",
                    arguments_json="{}", status="dispatch_unknown"))
    with pytest.raises(DependencyPlanError):
        _final(Session, owner, cfg, tid, fence, evolved)
    with Session() as db:
        assert db.get(Turn, tid).state == "running"
        assert db.get(PlanExecutionClaim, cid) is not None
        assert db.get(DependencyWorkerFence, cid).state == "active"


def test_two_step_terminal_crash_rolls_back_claim_fence_and_turn(store):
    Session, cid = store
    prior, old, cfg, tid, fence, did, evolved = _complete(Session, cid)
    target = Session()
    def fail_on_assistant(sess, ctx, instances):
        if any(isinstance(m, Message) and m.role == "assistant"
               for m in sess.new):
            raise RuntimeError("inject second-turn finalizer crash")
    event.listen(target, "before_flush", fail_on_assistant)
    try:
        with pytest.raises(RuntimeError, match="inject second-turn finalizer crash"):
            with target.begin():
                finalize_second_revised_turn_in_session(
                    target, settings=cfg, owner_id=prior.owner_id,
                    conversation_id=cid, turn_id=tid,
                    request_id="continuation-"+tid,
                    expected_raw=evolved, fence=fence)
    finally:
        target.close()
    with Session() as db:
        assert db.get(Turn, tid).state == "running"
        assert db.get(PlanExecutionClaim, cid).snapshot == evolved
        assert db.get(DependencyWorkerFence, cid).state == "active"
        assert db.get(ConversationPlan, cid).snapshot == evolved
    assert _final(Session, prior, cfg, tid, fence, evolved) == "completed"
