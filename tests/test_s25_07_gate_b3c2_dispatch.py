"""B3c-2 bounded internal Dispatch tests; never invokes a tool.

Uses B3a's real physical origin/Attempt fixture and a freshly persisted
B3c-1 approved anchor. SQLite and isolated real PostgreSQL both run in CI.
"""
import hashlib
import json
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from agent.dependency_plan_schema import DependencyPlanError
from agent.dependency_revision_host import bind_revised_plan_read
from services.db import (
    AgentAttempt, ConversationPlan, DependencyRevisionAnchor,
    DependencyV2StepReservation, Message, PlanToolDispatch,
    PrimaryRouteEvidence, Turn,
)
from services.dependency_fencing import acquire_dependency_fence
from services.dependency_revision_dispatch import begin_revised_dependency_dispatch_in_session
from services.dependency_revision_fingerprint import revision_fingerprint
from services.plan_dispatch import PlanDispatchJournal, DispatchConflict
from test_s25_07_gate_b3a_lineage import (
    store, seed, _seed_revision_resume, _b3b_host, _b3b_settings,
)


def _start(Session, cid, *, completed=False, enabled=True):
    raw, revision_tid, historical = seed(Session, cid, completed=completed)
    candidate = json.loads(raw)
    with Session.begin() as db:
        db.add(DependencyRevisionAnchor(
            conversation_id=cid, revision_turn_id=revision_tid,
            approved_candidate_json=raw,
            approved_digest=revision_fingerprint(candidate),
            parent_snapshot_sha256=candidate["lineage"]["parent_snapshot_sha256"],
            origin_refs_json=json.dumps(candidate["lineage"]["origin_refs"]),
        ))
    tid, message = _seed_revision_resume(Session, cid, raw)
    with Session.begin() as db:
        db.get(PrimaryRouteEvidence, tid).approved_request_digest = hashlib.sha256(
            message.encode("utf-8")).hexdigest()
    cfg = _b3b_settings().model_copy(
        update={"s25_07_revision_dispatch_intent_enabled":enabled})
    binding = bind_revised_plan_read(
        Session, settings=cfg, decision=_b3b_host(raw, message),
        owner_id="b3a-owner", conversation_id=cid,
        turn_id=tid, request_id="b3b-"+tid,
        expected_raw=raw, initial_turn_raw=raw,
        step_id=2 if completed else 1,
    )
    fence = acquire_dependency_fence(
        Session, conversation_id=cid, turn_id=tid, expected_raw=raw)
    return raw, binding, fence, cfg, historical


def _begin(Session, raw, binding, fence, settings):
    with Session.begin() as db:
        return begin_revised_dependency_dispatch_in_session(
            db, settings=settings, binding=binding, expected_raw=raw,
            initial_turn_raw=raw, fence=fence)


@pytest.mark.parametrize("completed", [False, True])
def test_b3c2_atomic_intent_binds_authorized_first_pending(store, completed):
    Session, cid = store
    raw, binding, fence, cfg, historical = _start(
        Session, cid, completed=completed)
    old = None
    if historical:
        with Session() as db:
            d = db.get(PlanToolDispatch, historical)
            old = (d.status, d.arguments_json, d.receipt_json)
    dispatched = _begin(Session, raw, binding, fence, cfg)
    with Session() as db:
        d = db.get(PlanToolDispatch, dispatched)
        reservation = db.get(DependencyV2StepReservation, (cid, binding.step_id))
        assert d.status == "dispatch_unknown"
        assert d.plan_revision == 2
        assert d.receipt_json is None
        assert d.worker_generation == fence.generation
        assert d.worker_token_digest == hashlib.sha256(
            fence.secret.encode("utf-8")).hexdigest()
        assert json.loads(d.arguments_json) == binding.arguments
        assert reservation.dispatch_id == dispatched
        assert reservation.turn_id == binding.turn_id
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(AgentAttempt, dispatched) is None
        if historical:
            source = db.get(PlanToolDispatch, historical)
            assert (source.status, source.arguments_json, source.receipt_json) == old
    with pytest.raises(DependencyPlanError):
        _begin(Session, raw, binding, fence, cfg)
    with Session() as db:
        assert db.query(DependencyV2StepReservation).filter_by(
            conversation_id=cid).count() == 1


@pytest.mark.parametrize("tamper", [
    "disabled", "wrong_digest", "message", "claim",
    "host", "original_approval", "anchor", "old_fence",
    "plan", "task", "tool", "source_attempt",
])
def test_b3c2_tampered_prepared_authority_never_dispatches(store, tamper):
    from dataclasses import replace
    from services.db import AgentTaskState, PlanExecutionClaim, DependencyWorkerFence
    Session, cid = store
    raw, binding, fence, cfg, historical = _start(
        Session, cid, completed=True)
    if tamper == "disabled":
        cfg = _b3b_settings()
    elif tamper == "wrong_digest":
        binding = replace(binding, arguments_digest="a"*64)
    elif tamper == "tool":
        binding = replace(binding, tool_name="calculate")
    elif tamper == "old_fence":
        fence = replace(fence, generation=fence.generation+1)
    else:
        with Session.begin() as db:
            if tamper == "message":
                db.get(Message, db.get(Turn, binding.turn_id).user_message_id).content = "被修改"
            elif tamper == "claim":
                db.get(PlanExecutionClaim, cid).snapshot = "{}"
            elif tamper == "host":
                db.get(PrimaryRouteEvidence, binding.turn_id).approved_request_digest = "0"*64
            elif tamper == "original_approval":
                db.get(PrimaryRouteEvidence, binding.approved_revision_turn_id).disposition = "deny"
            elif tamper == "anchor":
                db.get(DependencyRevisionAnchor, cid).approved_digest = "e"*64
            elif tamper == "plan":
                db.get(ConversationPlan, cid).snapshot = "{}"
            elif tamper == "task":
                db.get(AgentTaskState, cid).step_states = "[]"
            elif tamper == "source_attempt":
                db.get(AgentAttempt, historical).result_projection = "{}"
    with pytest.raises(DependencyPlanError):
        _begin(Session, raw, binding, fence, cfg)
    with Session() as db:
        assert db.query(DependencyV2StepReservation).filter_by(
            conversation_id=cid).count() == 0
        assert db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, plan_revision=2).count() == 0


def test_b3c2_new_dispatch_is_not_a_legacy_receipt_permit(store):
    Session, cid = store
    raw, binding, fence, cfg, _ = _start(Session, cid)
    dispatched = _begin(Session, raw, binding, fence, cfg)
    journal = PlanDispatchJournal(Session, cid, binding.turn_id)
    with pytest.raises(DispatchConflict, match="revision2_receipt_not_wired"):
        journal.record_receipt(dispatched, {"ok": True, "definition": "forged"}, fence=fence)
    with Session() as db:
        assert db.get(PlanToolDispatch, dispatched).status == "dispatch_unknown"
        assert db.get(PlanToolDispatch, dispatched).receipt_json is None


def test_b3c2_concurrent_logical_step_reservation_allows_one_winner(store):
    # The physical PG worker competition must also pass with separate sessions.
    # No tool is invoked, even by the winner.
    from concurrent.futures import ThreadPoolExecutor
    Session, cid = store
    raw, binding, fence, cfg, _ = _start(Session, cid)
    def worker(_):
        try:
            return _begin(Session, raw, binding, fence, cfg)
        except (DependencyPlanError, IntegrityError):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, (1, 2)))
    assert sum(x is not None for x in results) == 1
    with Session() as db:
        assert db.query(PlanToolDispatch).filter_by(
            conversation_id=cid, plan_revision=2).count() == 1
        assert db.query(DependencyV2StepReservation).filter_by(
            conversation_id=cid).count() == 1
