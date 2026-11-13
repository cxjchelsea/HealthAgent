"""S25-07 B3c-4 Slice A: real-DB post-checkpoint physical proof.

No tool invocation, Claim release, recovery, HTTP, or future execution authority.
"""
import hashlib
import json

import pytest

from agent.dependency_plan_schema import DependencyPlanError
from services.db import (
    AgentAttempt, AgentStateTransition, AgentTaskState, ConversationPlan,
    DependencyRevisionAnchor, DependencyV2StepReservation, PlanToolDispatch,
)
from services.dependency_revision_progress import verify_revised_checkpoint_progress
from test_s25_07_gate_b3c3_result import store, _prepared, _receipt, _checkpoint


def _completed(Session, cid, *, inherited=False):
    raw, binding, fence, settings, did, _ = _prepared(
        Session, cid, completed=inherited)
    observation = ({"ok": True, "characters": len(binding.arguments["text"]),
                    "words": len(binding.arguments["text"].split())}
                   if inherited else {"ok": True, "definition": "test definition"})
    _receipt(Session, raw, binding, fence, settings, did, observation)
    completed = _checkpoint(Session, raw, binding, fence, settings, did)
    with Session() as db:
        latest = db.get(ConversationPlan, cid).snapshot
    assert json.loads(latest) == completed
    return binding, did, latest


@pytest.mark.parametrize("inherited", [False, True])
def test_post_checkpoint_proof_from_physical_sources(store, inherited):
    Session, cid = store
    binding, did, latest = _completed(Session, cid, inherited=inherited)
    result = verify_revised_checkpoint_progress(
        Session, owner_id=binding.owner_id,
        conversation_id=cid, expected_raw=latest)
    assert result.completed_v2_step_id == binding.step_id
    assert result.dispatch_id == did
    assert result.snapshot_sha256 == hashlib.sha256(
        latest.encode("utf-8")).hexdigest()
    assert result.revision_turn_id == binding.approved_revision_turn_id
    with pytest.raises(DependencyPlanError):
        verify_revised_checkpoint_progress(
            Session, owner_id="another-owner",
            conversation_id=cid, expected_raw=latest)


@pytest.mark.parametrize("tamper", [
    "unverified_snapshot", "dispatch_status", "dispatch_args", "receipt",
    "attempt", "reservation", "task", "transition", "anchor",
    "another_dispatch", "worker_generation",
])
def test_post_checkpoint_rejects_forgery(store, tamper):
    from services.db import Turn
    import uuid
    Session, cid = store
    binding, did, latest = _completed(Session, cid, inherited=True)
    expected = latest
    with Session.begin() as db:
        if tamper == "unverified_snapshot":
            bogus = json.loads(latest)
            bogus["steps"][-1]["goal"] = "forged"
            expected = json.dumps(bogus, ensure_ascii=False)
            db.get(ConversationPlan, cid).snapshot = expected
            from services.db import PlanExecutionClaim
            db.get(PlanExecutionClaim, cid).snapshot = expected
        elif tamper == "dispatch_status":
            db.get(PlanToolDispatch, did).status = "observed"
        elif tamper == "dispatch_args":
            db.get(PlanToolDispatch, did).arguments_json = '{"text":"forged"}'
        elif tamper == "receipt":
            db.get(PlanToolDispatch, did).receipt_json = '{"ok":true,"characters":999,"words":1}'
        elif tamper == "attempt":
            db.get(AgentAttempt, did).result_projection = "{}"
        elif tamper == "reservation":
            db.get(DependencyV2StepReservation, (cid, binding.step_id)).turn_id = binding.approved_revision_turn_id
        elif tamper == "task":
            db.get(AgentTaskState, cid).step_states = "[]"
        elif tamper == "transition":
            db.query(AgentStateTransition).filter_by(
                conversation_id=cid, reason_code="verified_step_completed",
                step_id=binding.step_id).one().attempt_id = str(uuid.uuid4())
        elif tamper == "anchor":
            db.get(DependencyRevisionAnchor, cid).approved_digest = "0"*64
        elif tamper == "another_dispatch":
            db.add(PlanToolDispatch(
                id=str(uuid.uuid4()), conversation_id=cid,
                turn_id=binding.turn_id, step_id=999,
                tool_name="text_stats", tool_version="1.0.0",
                plan_revision=2, arguments_json="{}",
                status="dispatch_unknown"))
        elif tamper == "worker_generation":
            db.get(PlanToolDispatch, did).worker_generation += 1
    with pytest.raises(DependencyPlanError):
        verify_revised_checkpoint_progress(
            Session, owner_id=binding.owner_id,
            conversation_id=cid, expected_raw=expected)


def test_uncheckpointed_or_failed_dispatch_is_not_progress(store):
    Session, cid = store
    raw, binding, fence, cfg, did, _ = _prepared(Session, cid)
    with pytest.raises(DependencyPlanError):
        verify_revised_checkpoint_progress(
            Session, owner_id=binding.owner_id,
            conversation_id=cid, expected_raw=raw)
    _receipt(Session, raw, binding, fence, cfg, did, {
        "ok": False, "error": "synthetic failure"})
    with pytest.raises(DependencyPlanError):
        verify_revised_checkpoint_progress(
            Session, owner_id=binding.owner_id,
            conversation_id=cid, expected_raw=raw)
