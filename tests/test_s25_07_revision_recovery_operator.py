"""S25-07 operator-gated second Turn recovery (SQLite and PostgreSQL).

Independent trust verifier stubs here represent external identity and confirmed
worker shutdown; flags/digests alone cannot authorize production release.
"""
import json

import pytest
from sqlalchemy import event

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, ConversationPlan, DependencyV2StepReservation,
    DependencyWorkerFence, Message, PlanExecutionClaim, PlanRecoveryAudit,
    PlanToolDispatch, Turn,
)
from services.dependency_fencing import assert_dependency_fence_in_session
from services.dependency_revision_recovery_inspect import inspect_revised_second_turn
from services.dependency_revision_recovery_operator import reconcile_revised_second_orphan
from test_s25_07_revision_recovery_inspect import store, _setup
from test_s25_07_revision_second import _receipt


def _snapshot(Session, first, tid, raw):
    return inspect_revised_second_turn(
        Session, owner_id=first.owner_id, conversation_id=first.conversation_id,
        turn_id=tid, expected_raw=raw)


def _recover(Session, first, tid, raw, report, *,
             operator_authorized=True, worker_terminated_confirmed=True,
             access=None, stopped=None,
             evidence=None, plan=None):
    return reconcile_revised_second_orphan(
        Session, owner_id=first.owner_id, conversation_id=first.conversation_id,
        turn_id=tid, expected_raw=raw, operator_id="reviewed-operator-7",
        expected_plan_sha256=plan or report.plan_sha256,
        expected_evidence_sha256=evidence or report.evidence_sha256,
        operator_authorized=operator_authorized,
        worker_terminated_confirmed=worker_terminated_confirmed,
        operator_access_verifier=access if access is not None else
            (lambda operator, owner, cid, turn: True),
        worker_termination_verifier=stopped if stopped is not None else
            (lambda cid, turn: True),
    )


@pytest.mark.parametrize("observed", ["reserved", "uncertain", "failed"])
def test_v2_operator_releases_only_after_verified_termination(store, observed):
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    if observed == "uncertain":
        with Session.begin() as db:
            db.get(PlanToolDispatch, did).status = "invoking"
    elif observed == "failed":
        _receipt(Session, first, cfg, tid, fence, raw, did,
                 {"ok": False, "error": "timeout"})
    report = _snapshot(Session, first, tid, raw)
    events = []
    def access(operator, owner, conversation, turn):
        events.append(("access", operator, owner, conversation, turn))
        return True
    def stopped(conversation, turn):
        events.append(("stopped", conversation, turn))
        return True
    assert _recover(Session, first, tid, raw, report,
                    access=access, stopped=stopped) == report
    assert [e[0] for e in events] == ["access", "stopped"]
    with Session() as db:
        turn = db.get(Turn, tid)
        assert turn.state == "partial"
        assert turn.failure_code == (
            "dependency_verified_failure" if observed == "failed"
            else "dependency_reconciliation_required")
        assistant = db.get(Message, turn.assistant_message_id)
        assert assistant.role == "assistant" and assistant.status == "partial"
        assert turn.output_chars == len(assistant.content)
        assert db.get(PlanExecutionClaim, cid) is None
        assert db.get(DependencyWorkerFence, cid).state == "fenced"
        row = db.get(PlanToolDispatch, did)
        assert row.status == {
            "reserved": "dispatch_unknown",
            "uncertain": "invoking", "failed": "observed_failure",
        }[observed]
        assert db.get(AgentAttempt, did) is None
        assert db.get(ConversationPlan, cid).snapshot == raw
        assert db.get(DependencyV2StepReservation, (cid, 2)).dispatch_id == did
        audits = db.query(PlanRecoveryAudit).filter_by(
            conversation_id=cid, turn_id=tid).all()
        assert len(audits) == 1
        assert audits[0].decision == "dependency_v2_orphan_released_no_replay"
        assert audits[0].evidence_sha256 == report.evidence_sha256
    with pytest.raises(DependencyPlanError):
        _recover(Session, first, tid, raw, report)
    with pytest.raises(DependencyPlanError):
        with Session.begin() as db:
            assert_dependency_fence_in_session(
                db, conversation_id=cid, turn_id=tid, fence=fence)


@pytest.mark.parametrize("failure", [
    "no_operator_flag", "no_worker_flag", "access_denied",
    "worker_not_stopped", "access_exception", "worker_exception",
    "stale_evidence", "stale_plan", "missing_access_verifier",
    "missing_worker_verifier", "boolean_like_access", "boolean_like_worker",
])
def test_v2_recovery_denies_untrusted_authority(store, failure):
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    report = _snapshot(Session, first, tid, raw)
    kwargs = {}
    if failure == "no_operator_flag":
        kwargs["operator_authorized"] = False
    elif failure == "no_worker_flag":
        kwargs["worker_terminated_confirmed"] = False
    elif failure == "access_denied":
        kwargs["access"] = lambda *a: False
    elif failure == "worker_not_stopped":
        kwargs["stopped"] = lambda *a: False
    elif failure == "access_exception":
        kwargs["access"] = lambda *a: 1 / 0
    elif failure == "worker_exception":
        kwargs["stopped"] = lambda *a: 1 / 0
    elif failure == "stale_evidence":
        kwargs["evidence"] = "a" * 64
    elif failure == "stale_plan":
        kwargs["plan"] = "b" * 64
    elif failure == "missing_access_verifier":
        kwargs["access"] = False
    elif failure == "missing_worker_verifier":
        kwargs["stopped"] = False
    elif failure == "boolean_like_access":
        kwargs["access"] = lambda *a: 1
    elif failure == "boolean_like_worker":
        kwargs["stopped"] = lambda *a: 1
    with pytest.raises(DependencyPlanError):
        _recover(Session, first, tid, raw, report, **kwargs)
    with Session() as db:
        assert db.get(Turn, tid).state == "running"
        assert db.get(PlanExecutionClaim, cid) is not None
        assert db.get(DependencyWorkerFence, cid).state == "active"
        assert db.query(PlanRecoveryAudit).filter_by(
            conversation_id=cid, turn_id=tid).count() == 0


def test_v2_recovery_does_not_release_successful_uncheckpointed_receipt(store):
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    with Session() as db:
        args = json.loads(db.get(PlanToolDispatch, did).arguments_json)
    _receipt(Session, first, cfg, tid, fence, raw, did, {
        "ok": True, "characters": len(args["text"]),
        "words": len(args["text"].split())})
    report = _snapshot(Session, first, tid, raw)
    assert report.classification == "verified_receipt_checkpoint_required"
    with pytest.raises(DependencyPlanError,
                       match="dependency_v2_recovery_checkpoint_required"):
        _recover(Session, first, tid, raw, report)
    with Session() as db:
        assert db.get(Turn, tid).state == "running"
        assert db.get(PlanExecutionClaim, cid) is not None
        assert db.get(DependencyWorkerFence, cid).state == "active"


def test_v2_recovery_rechecks_physical_evidence_under_locked_transaction(store):
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    report = _snapshot(Session, first, tid, raw)
    with Session.begin() as db:
        db.get(PlanToolDispatch, did).status = "invoking"
    with pytest.raises(DependencyPlanError,
                       match="dependency_v2_recovery_evidence_changed"):
        _recover(Session, first, tid, raw, report)
    with Session() as db:
        assert db.get(PlanExecutionClaim, cid) is not None
        assert db.get(DependencyWorkerFence, cid).state == "active"


def test_v2_recovery_crash_rollback_does_not_partially_release(store):
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    report = _snapshot(Session, first, tid, raw)
    def flush_failure(sess, ctx, instances):
        if any(isinstance(m, PlanRecoveryAudit) for m in sess.new):
            raise RuntimeError("recovery audit crash")
    # Session.begin uses a fresh Session: attach to the sessionmaker's class
    event.listen(Session.class_, "before_flush", flush_failure)
    try:
        with pytest.raises(RuntimeError, match="recovery audit crash"):
            _recover(Session, first, tid, raw, report)
    finally:
        event.remove(Session.class_, "before_flush", flush_failure)
    with Session() as db:
        assert db.get(Turn, tid).state == "running"
        assert db.get(PlanExecutionClaim, cid) is not None
        assert db.get(DependencyWorkerFence, cid).state == "active"
        assert db.query(PlanRecoveryAudit).filter_by(conversation_id=cid).count() == 0
    assert _recover(Session, first, tid, raw, report) == report
