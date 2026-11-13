"""S25-07 operator-facing read-only v2 orphan classification.

No operator claims, worker termination, release or replay is attempted here.
"""
import json

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    ConversationPlan, DependencyWorkerFence, PlanExecutionClaim,
    PlanToolDispatch,
)
from services.dependency_revision_recovery_inspect import (
    inspect_revised_second_turn,
)
from services.dependency_revision_registry_runner import (
    invoke_second_revised_registry_read,
)
from test_s25_07_revision_second import (
    store, _prepare, _begin, _receipt,
)


def _state(Session, first, tid, raw):
    return inspect_revised_second_turn(
        Session, owner_id=first.owner_id,
        conversation_id=first.conversation_id,
        turn_id=tid, expected_raw=raw)


def _setup(Session, cid):
    first, _, cfg, tid, decision, fence, raw = _prepare(Session, cid)
    did = _begin(Session, first, cfg, tid, decision, fence, raw)
    return first, cfg, tid, fence, raw, did


def test_second_turn_orphan_inspector_classifies_reserved_without_replay(store):
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    one = _state(Session, first, tid, raw)
    assert one.dispatch_id == did
    assert one.classification == "reserved_no_safe_replay"
    assert len(one.plan_sha256) == len(one.evidence_sha256) == 64
    assert _state(Session, first, tid, raw) == one
    with Session() as db:
        assert db.get(PlanToolDispatch, did).status == "dispatch_unknown"
        assert db.get(PlanExecutionClaim, cid).snapshot == raw


def test_second_turn_orphan_inspector_classifies_invocation_crash(store, monkeypatch):
    from agent.tool_registry import READ_REGISTRY
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    cfg = cfg.model_copy(update={
        "s25_07_revision_registry_invocation_enabled": True})
    def crash(*a, **kw):
        raise RuntimeError("process interrupted")
    with monkeypatch.context() as m:
        m.setattr(READ_REGISTRY, "invoke", crash)
        with pytest.raises(RuntimeError):
            invoke_second_revised_registry_read(
                Session, settings=cfg, owner_id=first.owner_id,
                conversation_id=cid, turn_id=tid,
                request_id="continuation-"+tid,
                expected_raw=raw, initial_turn_raw=raw,
                fence=fence, dispatch_id=did)
    inspection = _state(Session, first, tid, raw)
    assert inspection.classification == "uncertain_no_replay"
    with Session() as db:
        assert db.get(PlanToolDispatch, did).receipt_json is None
        assert db.get(PlanExecutionClaim, cid) is not None


@pytest.mark.parametrize("success", [False, True])
def test_second_turn_orphan_inspector_classifies_observation(store, success):
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    with Session() as db:
        arguments = json.loads(db.get(PlanToolDispatch, did).arguments_json)
    result = ({"ok": True,
               "characters": len(arguments["text"]),
               "words": len(arguments["text"].split())}
              if success else {"ok": False, "error": "timeout"})
    _receipt(Session, first, cfg, tid, fence, raw, did, result)
    report = _state(Session, first, tid, raw)
    assert report.classification == (
        "verified_receipt_checkpoint_required" if success
        else "verified_failure_no_replay")


@pytest.mark.parametrize("tamper", [
    "wrong_owner", "old_fence", "claim", "receipt", "host",
    "dispatch_extra", "snapshot", "dispatch_generation", "dispatch_digest",
    "dispatch_version",
])
def test_second_turn_inspector_denies_corruption(store, tamper):
    from services.db import PrimaryRouteEvidence
    import uuid
    Session, cid = store
    first, cfg, tid, fence, raw, did = _setup(Session, cid)
    owner = first.owner_id
    if tamper == "wrong_owner":
        owner = "foreign"
    else:
        with Session.begin() as db:
            if tamper == "old_fence":
                db.get(DependencyWorkerFence, cid).state = "fenced"
            elif tamper == "claim":
                db.get(PlanExecutionClaim, cid).snapshot = "{}"
            elif tamper == "receipt":
                db.get(PlanToolDispatch, did).receipt_json = '{"ok":true}'
            elif tamper == "host":
                db.get(PrimaryRouteEvidence, tid).approved_request_digest = "0"*64
            elif tamper == "snapshot":
                db.get(ConversationPlan, cid).snapshot = "{}"
            elif tamper == "dispatch_generation":
                db.get(PlanToolDispatch, did).worker_generation += 1
            elif tamper == "dispatch_digest":
                db.get(PlanToolDispatch, did).worker_token_digest = "e" * 64
            elif tamper == "dispatch_version":
                db.get(PlanToolDispatch, did).tool_version = "9.9.9"
            elif tamper == "dispatch_extra":
                db.add(PlanToolDispatch(
                    id=str(uuid.uuid4()), conversation_id=cid, turn_id=tid,
                    plan_revision=2, step_id=777,
                    tool_name="text_stats", tool_version="1.0.0",
                    arguments_json="{}", status="dispatch_unknown"))
    with pytest.raises(DependencyPlanError):
        inspect_revised_second_turn(
            Session, owner_id=owner, conversation_id=cid,
            turn_id=tid, expected_raw=raw)
    with Session() as db:
        assert db.get(PlanExecutionClaim, cid) is not None
