"""B3c-3 internal-only receipt/checkpoint physical verification.

The observations here are synthetic. No tool execution or HTTP is enabled.
"""
import json
from dataclasses import replace

import pytest
from sqlalchemy import event

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentTaskState, AgentStateTransition, ConversationPlan,
    DependencyV2StepReservation, PlanExecutionClaim, PlanToolDispatch,
    PrimaryRouteEvidence, Message, Turn, DependencyRevisionAnchor,
)
from services.dependency_revision_result import (
    record_revised_receipt_in_session, checkpoint_revised_step_in_session,
)
from test_s25_07_gate_b3c2_dispatch import store, _start, _begin


def _prepared(Session, cid, *, completed=False, enabled=True):
    raw, binding, fence, settings, historical = _start(
        Session, cid, completed=completed)
    dispatch_id = _begin(Session, raw, binding, fence, settings)
    settings = settings.model_copy(update={
        "s25_07_revision_receipt_checkpoint_enabled": enabled})
    return raw, binding, fence, settings, dispatch_id, historical


def _receipt(Session, raw, binding, fence, settings, dispatch_id, observation):
    with Session.begin() as db:
        return record_revised_receipt_in_session(
            db, settings=settings, binding=binding, expected_raw=raw,
            initial_turn_raw=raw, fence=fence, dispatch_id=dispatch_id,
            observation=observation)


def _checkpoint(Session, raw, binding, fence, settings, dispatch_id):
    with Session.begin() as db:
        return checkpoint_revised_step_in_session(
            db, settings=settings, binding=binding, expected_raw=raw,
            initial_turn_raw=raw, fence=fence, dispatch_id=dispatch_id)


@pytest.mark.parametrize("completed", [False, True])
def test_b3c3_success_receipt_then_atomic_checkpoint(store, completed):
    Session, cid = store
    raw, binding, fence, cfg, did, inherited = _prepared(
        Session, cid, completed=completed)
    obs = ({"ok": True, "characters": len(binding.arguments["text"]),
            "words": len(binding.arguments["text"].split())}
           if completed else {"ok": True, "definition": "a verified test definition",
                              "source": "test"})
    projected = _receipt(Session, raw, binding, fence, cfg, did, obs)
    assert projected["ok"] is True
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "observed"
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(AgentAttempt, did) is None
    new_plan = _checkpoint(Session, raw, binding, fence, cfg, did)
    assert new_plan["executions"][str(binding.step_id)]["plan_revision"] == 2
    assert next(s for s in new_plan["steps"] if s["id"] == binding.step_id)[
        "status"] == "completed"
    with Session() as db:
        d = db.get(PlanToolDispatch, did)
        attempt = db.get(AgentAttempt, did)
        task = db.get(AgentTaskState, cid)
        assert d.status == "checkpointed"
        assert attempt.status == "succeeded"
        assert json.loads(attempt.result_projection) == projected
        assert task.plan_revision == 2
        assert task.state in ("partial", "completed")
        assert json.loads(db.get(ConversationPlan, cid).snapshot) == new_plan
        assert json.loads(db.get(PlanExecutionClaim, cid).snapshot) == new_plan
        assert db.get(DependencyV2StepReservation, (cid, binding.step_id)).dispatch_id == did
    with pytest.raises(DependencyPlanError):
        _checkpoint(Session, raw, binding, fence, cfg, did)
    with pytest.raises(DependencyPlanError):
        _receipt(Session, raw, binding, fence, cfg, did, obs)


def test_b3c3_negative_receipt_never_advances_or_retries(store):
    Session, cid = store
    raw, binding, fence, cfg, did, _ = _prepared(Session, cid)
    projected = _receipt(Session, raw, binding, fence, cfg, did, {
        "ok": False, "error": "synthetic timeout"})
    assert projected["ok"] is False
    with pytest.raises(DependencyPlanError):
        _checkpoint(Session, raw, binding, fence, cfg, did)
    with pytest.raises(DependencyPlanError):
        _receipt(Session, raw, binding, fence, cfg, did, {"ok": True, "definition": "forged"})
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "observed_failure"
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(PlanExecutionClaim, cid).snapshot == raw
        assert db.get(AgentAttempt, did) is None


@pytest.mark.parametrize("tamper", [
    "gate", "claim", "plan", "host", "message", "fence",
    "reservation", "worker", "arguments", "receipt", "step",
    "origin_assistant", "origin_user", "origin_transition",
])
def test_b3c3_checkpoint_guards_fail_closed(store, tamper):
    Session, cid = store
    raw, binding, fence, cfg, did, _ = _prepared(Session, cid)
    _receipt(Session, raw, binding, fence, cfg, did, {
        "ok": True, "definition": "bounded", "source": "test"})
    if tamper == "gate":
        cfg = cfg.model_copy(update={
            "s25_07_revision_receipt_checkpoint_enabled": False})
    elif tamper == "fence":
        fence = replace(fence, generation=fence.generation + 1)
    else:
        with Session.begin() as db:
            if tamper == "claim":
                db.get(PlanExecutionClaim, cid).snapshot = "{}"
            elif tamper == "plan":
                db.get(ConversationPlan, cid).snapshot = "{}"
            elif tamper == "host":
                db.get(PrimaryRouteEvidence, binding.turn_id).approved_request_digest = "f"*64
            elif tamper == "message":
                mid = db.get(Turn, binding.turn_id).user_message_id
                db.get(Message, mid).content = "maliciously changed"
            elif tamper == "reservation":
                db.get(DependencyV2StepReservation,
                       (cid, binding.step_id)).turn_id = binding.approved_revision_turn_id
            elif tamper == "worker":
                db.get(PlanToolDispatch, did).worker_generation += 1
            elif tamper == "arguments":
                db.get(PlanToolDispatch, did).arguments_json = "{}"
            elif tamper == "receipt":
                db.get(PlanToolDispatch, did).receipt_json = '{"ok":true,"definition_availability":"truncated"}'
            elif tamper == "step":
                binding = replace(binding, step_id=binding.step_id + 1)
            elif tamper in ("origin_assistant", "origin_user"):
                original = db.get(DependencyRevisionAnchor, cid)
                saved_turn = db.get(Turn, original.revision_turn_id)
                mid = (saved_turn.assistant_message_id
                       if tamper == "origin_assistant"
                       else saved_turn.user_message_id)
                db.get(Message, mid).status = "pending"
            elif tamper == "origin_transition":
                original = db.query(AgentStateTransition).filter_by(
                    conversation_id=cid,
                    reason_code="dependency_plan_revision_saved").one()
                original.entity_type = "attempt"
    with pytest.raises(DependencyPlanError):
        _checkpoint(Session, raw, binding, fence, cfg, did)
    with Session() as db:
        assert db.get(AgentAttempt, did) is None
        assert db.get(PlanToolDispatch, did).status == "observed"


def test_b3c3_checkpoint_rollback_is_atomic(store):
    Session, cid = store
    raw, binding, fence, cfg, did, _ = _prepared(Session, cid)
    _receipt(Session, raw, binding, fence, cfg, did, {
        "ok": True, "definition": "safe", "source": "test"})
    task_before = None
    with Session() as db:
        task_before = (db.get(AgentTaskState, cid).revision,
                       db.get(AgentTaskState, cid).step_states)
    db = Session()
    def break_before_commit(session, flush_context, instances):
        if any(isinstance(x, AgentAttempt) for x in session.new):
            raise RuntimeError("injected_checkpoint_failure")
    event.listen(db, "before_flush", break_before_commit)
    try:
        with pytest.raises(RuntimeError, match="injected_checkpoint_failure"):
            with db.begin():
                checkpoint_revised_step_in_session(
                    db, settings=cfg, binding=binding, expected_raw=raw,
                    initial_turn_raw=raw, fence=fence, dispatch_id=did)
    finally:
        db.close()
    with Session() as verify:
        assert verify.get(AgentAttempt, did) is None
        assert verify.get(PlanToolDispatch, did).status == "observed"
        assert verify.get(ConversationPlan, cid).snapshot == raw
        assert verify.get(PlanExecutionClaim, cid).snapshot == raw
        task = verify.get(AgentTaskState, cid)
        assert (task.revision, task.step_states) == task_before
    assert _checkpoint(Session, raw, binding, fence, cfg, did)
