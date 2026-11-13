"""S25-07 integrated milestone 2: next Turn claim after closed v2 step.

These tests do not authorize any tool effect, Dispatch, retry or HTTP.
"""
import hashlib
import json
import uuid
from dataclasses import replace

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    ConversationPlan, DependencyWorkerFence, Message, PlanExecutionClaim,
    PlanToolDispatch, PrimaryRouteEvidence, Turn,
)
from services.dependency_revision_continuation import (
    admit_revised_continuation_claim_in_session,
)
from services.dependency_fencing import acquire_dependency_fence
from services.primary_route_evidence import snapshot_digest
from test_s25_07_revision_terminal import store, _fixture, _close
from test_s25_07_gate_b3a_lineage import _b3b_host


def _new_turn(Session, binding, raw, message="请继续执行修订后的已保存计划"):
    mid, tid = str(uuid.uuid4()), str(uuid.uuid4())
    with Session.begin() as db:
        db.add(Message(id=mid, conversation_id=binding.conversation_id,
                       role="user", content=message, status="completed"))
        db.flush()
        db.add(Turn(id=tid, conversation_id=binding.conversation_id,
                    request_id="continuation-"+tid, user_message_id=mid,
                    state="running"))
        db.flush()
        db.add(PrimaryRouteEvidence(
            turn_id=tid, conversation_id=binding.conversation_id,
            proposal_id=str(uuid.uuid4()), schema_version="primary-route-v1",
            route_kind="plan_resume", semantic_attempts=1,
            preturn_snapshot_digest=snapshot_digest(raw),
            disposition="allow",
            admission_reason="dependency_revision_resume_prepare_only",
            approved_request_digest=hashlib.sha256(
                message.encode("utf-8")).hexdigest(),
        ))
    return tid, message


def _setup(Session, cid):
    binding, old_fence, cfg, did, raw = _fixture(Session, cid, inherited=False)
    assert _close(Session, binding, old_fence, cfg, raw) == "partial"
    new_tid, text = _new_turn(Session, binding, raw)
    cfg = cfg.model_copy(update={
        "s25_07_revision_continuation_enabled": True})
    decision = _b3b_host(raw, text)
    return binding, old_fence, cfg, new_tid, decision, raw


def _admit(Session, binding, cfg, tid, decision, raw):
    with Session.begin() as db:
        return admit_revised_continuation_claim_in_session(
            db, settings=cfg, decision=decision,
            owner_id=binding.owner_id, conversation_id=binding.conversation_id,
            turn_id=tid, request_id="continuation-"+tid,
            expected_raw=raw, initial_turn_raw=raw)


def test_new_turn_reclaims_after_verified_v2_terminal(store):
    Session, cid = store
    binding, old_fence, cfg, new_tid, decision, raw = _setup(Session, cid)
    next_step = _admit(Session, binding, cfg, new_tid, decision, raw)
    assert next_step == 2
    with Session() as db:
        assert db.get(PlanExecutionClaim, cid).turn_id == new_tid
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(DependencyWorkerFence, cid).state == "fenced"
        assert db.get(Turn, binding.turn_id).state == "partial"
        assert db.get(Turn, new_tid).state == "running"
        assert db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, plan_revision=2).count() == 1
    with pytest.raises(DependencyPlanError):
        _admit(Session, binding, cfg, new_tid, decision, raw)
    # Only after Claim is committed may a new worker generation be minted.
    token = acquire_dependency_fence(
        Session, conversation_id=cid, turn_id=new_tid,
        expected_raw=raw)
    assert token.generation == old_fence.generation + 1
    assert token.secret != old_fence.secret


@pytest.mark.parametrize("tamper", [
    "gate", "wrong_owner", "request", "host_sha",
    "stale_snapshot", "prior_terminal_message",
    "old_dispatch", "wrong_route", "previous_fence",
    "old_worker_digest", "old_worker_generation",
])
def test_next_turn_claim_rejects_unverified_authority(store, tamper):
    Session, cid = store
    binding, old_fence, cfg, tid, decision, raw = _setup(Session, cid)
    owner = binding.owner_id
    if tamper == "gate":
        cfg = cfg.model_copy(update={"s25_07_revision_continuation_enabled": False})
    elif tamper == "wrong_owner":
        owner = "other-owner"
    elif tamper == "wrong_route":
        decision = replace(decision, route_kind="plan_execute_first")
    elif tamper == "request":
        with Session.begin() as db:
            db.get(Message, db.get(Turn, tid).user_message_id).content = "tampered"
    elif tamper == "host_sha":
        with Session.begin() as db:
            db.get(PrimaryRouteEvidence, tid).approved_request_digest = "a"*64
    elif tamper == "stale_snapshot":
        with Session.begin() as db:
            db.get(ConversationPlan, cid).snapshot = "{}"
    elif tamper == "prior_terminal_message":
        with Session.begin() as db:
            old = db.get(Turn, binding.turn_id)
            db.get(Message, old.assistant_message_id).status = "running"
    elif tamper == "old_dispatch":
        with Session.begin() as db:
            old = db.query(PlanToolDispatch).filter_by(
                conversation_id=cid, plan_revision=2).one()
            old.receipt_json = '{"ok":true}'
    elif tamper in ("old_worker_digest", "old_worker_generation"):
        with Session.begin() as db:
            old = db.query(PlanToolDispatch).filter_by(
                conversation_id=cid, plan_revision=2).one()
            if tamper == "old_worker_digest":
                old.worker_token_digest = "a"*64
            else:
                old.worker_generation += 1
    elif tamper == "previous_fence":
        with Session.begin() as db:
            db.get(DependencyWorkerFence, cid).state = "active"
    with pytest.raises(DependencyPlanError):
        with Session.begin() as db:
            admit_revised_continuation_claim_in_session(
                db, settings=cfg, decision=decision,
                owner_id=owner, conversation_id=cid,
                turn_id=tid, request_id="continuation-"+tid,
                expected_raw=raw, initial_turn_raw=raw)
    with Session() as db:
        assert db.get(PlanExecutionClaim, cid) is None
        assert db.get(Turn, tid).state == "running"


def test_second_turn_admission_does_not_dispatch_or_change_evidence(store):
    Session, cid = store
    binding, old_fence, cfg, tid, decision, raw = _setup(Session, cid)
    with Session() as db:
        before = [(d.id, d.status, d.receipt_json) for d in
                  db.query(PlanToolDispatch).filter_by(conversation_id=cid).all()]
    assert _admit(Session, binding, cfg, tid, decision, raw) == 2
    with Session() as db:
        after = [(d.id, d.status, d.receipt_json) for d in
                 db.query(PlanToolDispatch).filter_by(conversation_id=cid).all()]
        assert before == after
        assert db.get(PlanExecutionClaim, cid).snapshot == raw
