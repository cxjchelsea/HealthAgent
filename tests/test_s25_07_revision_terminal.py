"""Revision2 integration: verified checkpoint -> terminal Turn + fenced Claim release.

Uses synthetic internal Receipt fixtures, no external tool invocation or HTTP.
"""
import json
from dataclasses import replace

import pytest
from sqlalchemy import event

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    ConversationPlan, DependencyWorkerFence, Message, PlanExecutionClaim,
    PlanToolDispatch, Turn,
)
from services.dependency_fencing import assert_dependency_fence_in_session
from services.dependency_revision_terminal import finalize_revised_checkpoint_turn_in_session
from test_s25_07_gate_b3c3_result import store, _prepared, _receipt, _checkpoint


def _fixture(Session, cid, *, inherited=False):
    raw, binding, fence, cfg, did, _ = _prepared(
        Session, cid, completed=inherited)
    result = ({"ok": True, "characters": len(binding.arguments["text"]),
               "words": len(binding.arguments["text"].split())}
              if inherited else {"ok": True, "definition": "verified test value"})
    _receipt(Session, raw, binding, fence, cfg, did, result)
    _checkpoint(Session, raw, binding, fence, cfg, did)
    with Session() as db:
        updated = db.get(ConversationPlan, cid).snapshot
    cfg = cfg.model_copy(update={
        "s25_07_revision_turn_terminal_enabled": True})
    return binding, fence, cfg, did, updated


def _close(Session, binding, fence, cfg, updated):
    with Session.begin() as db:
        return finalize_revised_checkpoint_turn_in_session(
            db, settings=cfg, owner_id=binding.owner_id,
            conversation_id=binding.conversation_id,
            expected_raw=updated, turn_id=binding.turn_id,
            fence=fence)


@pytest.mark.parametrize("inherited", [False, True])
def test_v2_verified_terminal_closes_and_fences_atomically(store, inherited):
    Session, cid = store
    binding, fence, cfg, did, updated = _fixture(
        Session, cid, inherited=inherited)
    result = _close(Session, binding, fence, cfg, updated)
    expected = "completed" if inherited else "partial"
    assert result == expected
    with Session() as db:
        turn = db.get(Turn, binding.turn_id)
        assert turn.state == expected
        assert turn.failure_code is None
        msg = db.get(Message, turn.assistant_message_id)
        assert msg.conversation_id == cid and msg.status == expected
        assert msg.role == "assistant"
        assert db.get(PlanExecutionClaim, cid) is None
        assert db.get(ConversationPlan, cid).snapshot == updated
        assert db.get(PlanToolDispatch, did).status == "checkpointed"
        row = db.get(DependencyWorkerFence, cid)
        assert row.state == "fenced" and row.generation == fence.generation
    with pytest.raises(DependencyPlanError):
        _close(Session, binding, fence, cfg, updated)
    with pytest.raises(DependencyPlanError):
        with Session.begin() as db:
            assert_dependency_fence_in_session(
                db, conversation_id=cid,
                turn_id=binding.turn_id, fence=fence)


@pytest.mark.parametrize("invalid", [
    "gate_off", "stale_fence", "wrong_turn", "claim",
    "unverified_receipt", "changed_snapshot", "assistant_corrupt",
])
def test_v2_terminal_fail_closed_without_releasing_claim(store, invalid):
    Session, cid = store
    binding, fence, cfg, did, updated = _fixture(Session, cid)
    if invalid == "gate_off":
        cfg = cfg.model_copy(update={
            "s25_07_revision_turn_terminal_enabled": False})
    elif invalid == "stale_fence":
        fence = replace(fence, generation=fence.generation + 1)
    elif invalid == "wrong_turn":
        fence = replace(fence, turn_id=binding.approved_revision_turn_id)
    else:
        with Session.begin() as db:
            if invalid == "claim":
                db.get(PlanExecutionClaim, cid).snapshot = "{}"
            elif invalid == "unverified_receipt":
                db.get(PlanToolDispatch, did).receipt_json = '{"ok":true}'
            elif invalid == "changed_snapshot":
                db.get(ConversationPlan, cid).snapshot = "{}"
            elif invalid == "assistant_corrupt":
                msg = Message(
                    id="untrusted-assistant", conversation_id=cid,
                    role="user", content="forged", status="completed")
                db.add(msg)
                db.get(Turn, binding.turn_id).assistant_message_id = msg.id
    with pytest.raises(DependencyPlanError):
        _close(Session, binding, fence, cfg, updated)
    with Session() as db:
        assert db.get(PlanExecutionClaim, cid) is not None
        assert db.get(DependencyWorkerFence, cid).state == "active"
        assert db.get(Turn, binding.turn_id).state == "running"


def test_v2_terminal_rollback_keeps_claim_and_active_fence(store):
    Session, cid = store
    binding, fence, cfg, did, updated = _fixture(Session, cid)
    db = Session()
    def injected(session, flush_context, instances):
        if any(isinstance(m, Message) and m.role == "assistant"
               for m in session.new):
            raise RuntimeError("injected_terminal_failure")
    event.listen(db, "before_flush", injected)
    try:
        with pytest.raises(RuntimeError, match="injected_terminal_failure"):
            with db.begin():
                finalize_revised_checkpoint_turn_in_session(
                    db, settings=cfg, owner_id=binding.owner_id,
                    conversation_id=cid, expected_raw=updated,
                    turn_id=binding.turn_id, fence=fence)
    finally:
        db.close()
    with Session() as verify:
        assert verify.get(Turn, binding.turn_id).state == "running"
        assert verify.get(PlanExecutionClaim, cid).snapshot == updated
        assert verify.get(DependencyWorkerFence, cid).state == "active"
        assert verify.get(ConversationPlan, cid).snapshot == updated
    assert _close(Session, binding, fence, cfg, updated) == "partial"
