"""S25-07 milestone 2B: first v2 Receipt in Turn A -> dependent v2 in Turn B.

Synthetic observations only; NO tool invocation, retry, HTTP or release of
the second Turn. Tests use persisted plan, receipt, claim and fence evidence.
"""
import json
import uuid

import pytest
from sqlalchemy import event

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentTaskState, ConversationPlan,
    DependencyV2StepReservation, DependencyWorkerFence,
    PlanExecutionClaim, PlanToolDispatch, Turn,
)
from services.dependency_fencing import acquire_dependency_fence
from services.dependency_revision_second import (
    begin_second_revised_dispatch_in_session,
    record_second_revised_receipt_in_session,
    checkpoint_second_revised_step_in_session,
)
from test_s25_07_revision_continuation import store, _setup, _admit


def _prepare(Session, cid):
    first, old_fence, cfg, tid, decision, raw = _setup(Session, cid)
    assert _admit(Session, first, cfg, tid, decision, raw) == 2
    new_fence = acquire_dependency_fence(
        Session, conversation_id=cid, turn_id=tid, expected_raw=raw)
    assert new_fence.generation == old_fence.generation + 1
    return first, old_fence, cfg, tid, decision, new_fence, raw


def _begin(Session, first, cfg, tid, decision, new_fence, raw):
    with Session.begin() as db:
        return begin_second_revised_dispatch_in_session(
            db, settings=cfg, decision=decision,
            owner_id=first.owner_id, conversation_id=first.conversation_id,
            turn_id=tid, request_id="continuation-"+tid,
            expected_raw=raw, initial_turn_raw=raw, fence=new_fence)


def _receipt(Session, first, cfg, tid, new_fence, raw, did, observation):
    with Session.begin() as db:
        return record_second_revised_receipt_in_session(
            db, settings=cfg, owner_id=first.owner_id,
            conversation_id=first.conversation_id, turn_id=tid,
            request_id="continuation-"+tid, expected_raw=raw,
            initial_turn_raw=raw, fence=new_fence, dispatch_id=did,
            observation=observation)


def _checkpoint(Session, first, cfg, tid, new_fence, raw, did):
    with Session.begin() as db:
        return checkpoint_second_revised_step_in_session(
            db, settings=cfg, owner_id=first.owner_id,
            conversation_id=first.conversation_id, turn_id=tid,
            request_id="continuation-"+tid, expected_raw=raw,
            initial_turn_raw=raw, fence=new_fence, dispatch_id=did)


def test_v2_dependent_second_step_across_turns_from_durable_receipt(store):
    Session, cid = store
    first, old, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    with Session() as db:
        second = db.get(PlanToolDispatch, did)
        first_dispatch = db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, plan_revision=2, step_id=1).one()
        definition = json.loads(first_dispatch.receipt_json)["definition"]
        assert json.loads(second.arguments_json) == {"text": definition}
        assert second.status == "dispatch_unknown"
        assert second.worker_generation == old.generation + 1
        assert db.get(DependencyV2StepReservation, (cid, 2)).dispatch_id == did
        assert db.get(AgentAttempt, did) is None
        assert db.get(ConversationPlan, cid).snapshot == raw
    with pytest.raises(DependencyPlanError):
        _begin(Session, first, cfg, tid, decision, fence, raw)
    observed = _receipt(Session, first, cfg, tid, fence, raw, did, {
        "ok": True, "characters": len(definition),
        "words": len(definition.split()),
    })
    assert observed == {"ok": True, "characters": len(definition),
                        "words": len(definition.split())}
    final = _checkpoint(Session, first, cfg, tid, fence, raw, did)
    assert final["executions"]["2"]["plan_revision"] == 2
    assert final["executions"]["2"]["source_evidence_refs"] == [{
        "source_dispatch_id": first_dispatch.id}]
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "checkpointed"
        assert db.get(AgentAttempt, did).status == "succeeded"
        assert db.get(AgentTaskState, cid).state == "completed"
        assert json.loads(db.get(ConversationPlan, cid).snapshot) == final
        assert json.loads(db.get(PlanExecutionClaim, cid).snapshot) == final
        assert db.get(Turn, tid).state == "running"
        assert db.get(DependencyWorkerFence, cid).state == "active"
    with pytest.raises(DependencyPlanError):
        _checkpoint(Session, first, cfg, tid, fence, raw, did)


def test_v2_second_failure_is_persisted_without_checkpoint_or_retry(store):
    Session, cid = store
    first, old, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    result = _receipt(Session, first, cfg, tid, fence, raw, did, {
        "ok": False, "error": "synthetic timeout"})
    assert result == {"ok": False, "error": "synthetic timeout"}
    with pytest.raises(DependencyPlanError):
        _checkpoint(Session, first, cfg, tid, fence, raw, did)
    with pytest.raises(DependencyPlanError):
        _receipt(Session, first, cfg, tid, fence, raw, did, {
            "ok": True, "characters": 1, "words": 1})
    with pytest.raises(DependencyPlanError):
        _begin(Session, first, cfg, tid, decision, fence, raw)
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "observed_failure"
        assert db.get(AgentAttempt, did) is None
        assert db.get(ConversationPlan, cid).snapshot == raw


@pytest.mark.parametrize("kind", [
    "wrong_count", "missing_count", "boolean_count", "wrong_source",
    "wrong_fence", "wrong_host", "wrong_claim", "disabled",
])
def test_v2_second_untrusted_receipt_or_binding_fails_closed(store, kind):
    from dataclasses import replace
    from services.db import PrimaryRouteEvidence
    Session, cid = store
    first, old, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    observation = {"ok": True, "characters": 19, "words": 3}
    with Session() as db:
        source = db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, plan_revision=2, step_id=1).one()
        definition = json.loads(source.receipt_json)["definition"]
    observation = {"ok": True, "characters": len(definition),
                   "words": len(definition.split())}
    if kind == "wrong_count":
        observation["characters"] += 1
    elif kind == "missing_count":
        del observation["words"]
    elif kind == "boolean_count":
        observation["words"] = True
    elif kind == "wrong_source":
        with Session.begin() as db:
            db.get(PlanToolDispatch, source.id).receipt_json = '{"ok":true,"definition":"changed"}'
    elif kind == "wrong_fence":
        fence = replace(fence, generation=fence.generation + 1)
    elif kind == "wrong_host":
        with Session.begin() as db:
            db.get(PrimaryRouteEvidence, tid).approved_request_digest = "a"*64
    elif kind == "wrong_claim":
        with Session.begin() as db:
            db.get(PlanExecutionClaim, cid).snapshot = "{}"
    elif kind == "disabled":
        cfg = cfg.model_copy(update={
            "s25_07_revision_continuation_enabled": False})
    with pytest.raises(DependencyPlanError):
        _receipt(Session, first, cfg, tid, fence, raw, did, observation)
    with Session() as db:
        d = db.get(PlanToolDispatch, did)
        assert d.status == "dispatch_unknown"
        assert d.receipt_json is None
        assert db.get(AgentAttempt, did) is None


def test_v2_second_checkpoint_injected_failure_rolls_back_everything(store):
    Session, cid = store
    first, old, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    with Session() as db:
        source = db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, plan_revision=2, step_id=1).one()
        definition = json.loads(source.receipt_json)["definition"]
    _receipt(Session, first, cfg, tid, fence, raw, did, {
        "ok": True, "characters": len(definition),
        "words": len(definition.split())})
    target = Session()
    def fail_on_attempt(sess, flush_ctx, instances):
        if any(isinstance(obj, AgentAttempt) for obj in sess.new):
            raise RuntimeError("injected second checkpoint failure")
    event.listen(target, "before_flush", fail_on_attempt)
    try:
        with pytest.raises(RuntimeError, match="injected second checkpoint failure"):
            with target.begin():
                checkpoint_second_revised_step_in_session(
                    target, settings=cfg, owner_id=first.owner_id,
                    conversation_id=cid, turn_id=tid,
                    request_id="continuation-"+tid, expected_raw=raw,
                    initial_turn_raw=raw, fence=fence, dispatch_id=did)
    finally:
        target.close()
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "observed"
        assert db.get(AgentAttempt, did) is None
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(PlanExecutionClaim, cid).snapshot == raw
    assert _checkpoint(Session, first, cfg, tid, fence, raw, did)
