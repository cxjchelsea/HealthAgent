"""Operator recovery must be evidence-bound, atomic, and never dispatch a tool."""
import hashlib
import json
import pytest
from sqlalchemy import select
from services.db import (Base, Conversation, ConversationPlan, PlanExecutionClaim,
                         PlanToolDispatch, PlanRecoveryAudit, Message, Turn, database)
from services.m4_offline_recovery import (quarantine, reconcile_verified_receipts, RecoveryRefused)


def fixture(tmp_path):
    dbfile = tmp_path / "recovery.db"
    url = f"sqlite:///{dbfile}"
    engine, Session = database(url)
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        c = Conversation(owner_id="test")
        db.add(c)
        db.flush()
        cid = c.id
        assistant = Message(conversation_id=cid, role="assistant", status="pending", content="")
        db.add(assistant)
        db.flush()
        t = Turn(conversation_id=cid, request_id="crash-turn-1", state="running",
                 assistant_message_id=assistant.id)
        db.add(t)
        db.flush()
        tid = t.id
        snapshot = json.dumps({"revision": 1, "steps": [
            {"id": 1, "status": "pending", "goal": "calculate",
             "expected_tool": "calculate", "expected_arguments": {"expression": "4*7"}}]})
        db.add(ConversationPlan(conversation_id=cid, snapshot=snapshot))
        db.add(PlanExecutionClaim(conversation_id=cid, turn_id=tid, snapshot=snapshot))
        db.add(PlanToolDispatch(conversation_id=cid, turn_id=tid, step_id=1,
                                tool_name="calculate", arguments_json='{"expression": "4*7"}',
                                status="dispatch_unknown"))
    evidence = tmp_path / "operator.json"
    valid = dict(decision="quarantine_without_replay", conversation_id=cid,
                 turn_id=tid, operator_id="local-maintainer",
                 external_effect_investigated=True, all_workers_stopped=True,
                 investigation_note="Checked independent tool logs; outcome uncertain.")
    evidence.write_text(json.dumps(valid), encoding="utf-8")
    return engine, Session, url, cid, tid, hashlib.sha256(snapshot.encode()).hexdigest(), evidence, valid


def test_orphan_quarantine_requires_exact_snapshot_and_attestation(tmp_path):
    engine, Session, url, cid, tid, hash_value, evidence, valid = fixture(tmp_path)
    args = dict(database_url=url, conversation_id=cid, turn_id=tid,
                snapshot_sha256=hash_value, evidence_file=evidence,
                operator_id="local-maintainer")
    try:
        with pytest.raises(RecoveryRefused, match="plan_snapshot_changed"):
            quarantine(**(args | {"snapshot_sha256": "0"*64}))
        with Session() as db:
            assert db.get(PlanExecutionClaim, cid) is not None
            assert db.get(ConversationPlan, cid) is not None
        bad = valid | {"all_workers_stopped": False}
        evidence.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(RecoveryRefused, match="incomplete_operator_attestation"):
            quarantine(**args)
        evidence.write_text(json.dumps(valid), encoding="utf-8")
        result = quarantine(**args)
        assert result["status"] == "quarantined_without_replay"
        with Session() as db:
            assert db.get(ConversationPlan, cid) is None
            assert db.get(PlanExecutionClaim, cid) is None
            turn = db.get(Turn, tid)
            assert turn.state == "provider_error"
            assert turn.failure_code == "plan_execution_manually_quarantined"
            entry = db.scalar(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid))
            assert entry.status == "quarantined_no_replay"
            audit = list(db.scalars(select(PlanRecoveryAudit).where(
                PlanRecoveryAudit.conversation_id == cid)))
            assert len(audit) == 1 and audit[0].snapshot_sha256 == hash_value
        with pytest.raises(RecoveryRefused, match="plan_missing"):
            quarantine(**args)
    finally:
        engine.dispose()


def test_reconcile_successful_persisted_calculate_receipt_without_tool_dispatch(tmp_path):
    engine, Session, url, cid, tid, hash_value, evidence, valid = fixture(tmp_path)
    args = dict(database_url=url, conversation_id=cid, turn_id=tid,
                snapshot_sha256=hash_value, evidence_file=evidence,
                operator_id="local-maintainer")
    valid["decision"] = "reconcile_verified_receipts"
    evidence.write_text(json.dumps(valid), encoding="utf-8")
    try:
        with pytest.raises(RecoveryRefused, match="receipt_unverified"):
            reconcile_verified_receipts(**args)
        with Session.begin() as db:
            entry = db.scalar(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid))
            entry.status = "observed"
            entry.receipt_json = json.dumps({"ok": True, "value": "28", "error": ""})
        result = reconcile_verified_receipts(**args)
        assert result["status"] == "receipts_reconciled_without_dispatch"
        assert result["remaining_steps"] is False
        with Session() as db:
            snapshot = json.loads(db.get(ConversationPlan, cid).snapshot)
            assert snapshot["steps"][0]["status"] == "completed"
            assert snapshot["steps"][0]["evidence_tool"] == "calculate"
            assert db.get(PlanExecutionClaim, cid) is None
            assert db.get(Turn, tid).state == "completed"
            entry = db.scalar(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid))
            assert entry.status == "checkpointed"
            aud = db.scalar(select(PlanRecoveryAudit).where(
                PlanRecoveryAudit.conversation_id == cid))
            assert aud.decision == "reconcile_verified_receipts"
        with pytest.raises(RecoveryRefused, match="turn_not_recoverable"):
            reconcile_verified_receipts(**args)
    finally:
        engine.dispose()
