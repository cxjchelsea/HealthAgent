"""M4 PostgreSQL subprocess crash recovery physical acceptance.

Only executes in the dedicated TEST_POSTGRES_URL database. Kills a spawned
test subprocess, never an API process. Preserves independent physical rows
through abrupt os._exit, then audits offline reconciliation or quarantine.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from services.db import (Conversation, ConversationPlan, Message, Turn,
                         PlanExecutionClaim, PlanToolDispatch, PlanRecoveryAudit, database)
from services.m4_offline_recovery import quarantine, reconcile_verified_receipts


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),
                    reason="requires isolated TEST_POSTGRES_URL")
@pytest.mark.parametrize("receipt_written", [False, True])
def test_real_subprocess_crash_postgres_durable_dispatch_recovery(
        tmp_path, receipt_written):
    url = os.environ["TEST_POSTGRES_URL"]
    assert url.startswith("postgresql"), "physical acceptance requires PostgreSQL"
    engine, Session = database(url)
    with Session.begin() as db:
        c = Conversation(owner_id="m4-crash-isolated-ci")
        db.add(c)
        db.flush()
        cid = c.id
        msg = Message(conversation_id=cid, role="assistant", status="pending", content="")
        db.add(msg)
        db.flush()
        t = Turn(conversation_id=cid, request_id="m4-crash-test-" + cid,
                 state="running", assistant_message_id=msg.id)
        db.add(t)
        db.flush()
        tid = t.id
        snapshot = json.dumps({"revision": 1, "next_step_id": 2, "steps": [
            {"id": 1, "goal": "calculate", "status": "pending",
             "expected_tool": "calculate",
             "expected_arguments": {"expression": "4*7"}}]}, ensure_ascii=False)
        db.add(ConversationPlan(conversation_id=cid, snapshot=snapshot))
    # Prevent inherited live connections. The subprocess owns its own engine.
    engine.dispose()
    script = """
import os, sys
from services.db import database
from services.plan_store import ConversationPlanStore
from services.plan_dispatch import PlanDispatchJournal
from agent.tools import execute_tool
url, cid, tid, raw, with_receipt = sys.argv[1:]
engine, Session = database(url)
with Session.begin() as db:
    ConversationPlanStore(Session, cid).acquire_execution_claim_in_session(
        db, turn_id=tid, expected_raw=raw)
journal = PlanDispatchJournal(Session, cid, tid)
did = journal.begin(1, 'calculate', {'expression': '4*7'})
if with_receipt == 'yes':
    result = execute_tool('calculate', {'expression': '4*7'}, [])
    assert result['ok'] and result['value'] == 28
    journal.record_receipt(did, result)
os._exit(73)
"""
    evidence_path = tmp_path / "evidence.json"
    evidence = {"decision": "reconcile_verified_receipts" if receipt_written
                else "quarantine_without_replay",
                "conversation_id": cid, "turn_id": tid,
                "operator_id": "ci-test-operator",
                "external_effect_investigated": True,
                "all_workers_stopped": True,
                "investigation_note": "Isolated CI subprocess terminated; independently verified DB state."}
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    try:
        child = subprocess.run(
            [sys.executable, "-c", script, url, cid, tid, snapshot,
             "yes" if receipt_written else "no"], cwd=str(Path(__file__).parents[1]),
            timeout=25, capture_output=True, text=True)
        assert child.returncode == 73, child.stderr
        engine, Session = database(url)
        with Session() as db:
            assert db.get(Turn, tid).state == "running"
            assert db.get(PlanExecutionClaim, cid) is not None
            journal = list(db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid)))
            assert len(journal) == 1
            assert journal[0].status == ("observed" if receipt_written
                                         else "dispatch_unknown")
            assert json.loads(db.get(ConversationPlan, cid).snapshot)["steps"][0]["status"] == "pending"
        action = reconcile_verified_receipts if receipt_written else quarantine
        outcome = action(database_url=url, conversation_id=cid, turn_id=tid,
                         snapshot_sha256=hashlib.sha256(snapshot.encode()).hexdigest(),
                         evidence_file=evidence_path, operator_id="ci-test-operator")
        assert outcome["status"] == ("receipts_reconciled_without_dispatch" if receipt_written
                                     else "quarantined_without_replay")
        with Session() as db:
            assert db.get(PlanExecutionClaim, cid) is None
            record = db.scalar(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == cid))
            assert record.status == ("checkpointed" if receipt_written
                                     else "quarantined_no_replay")
            plan = db.get(ConversationPlan, cid)
            if receipt_written:
                assert json.loads(plan.snapshot)["steps"][0]["status"] == "completed"
            else:
                assert plan is None
            assert db.scalar(select(PlanRecoveryAudit).where(
                PlanRecoveryAudit.conversation_id == cid)) is not None
    finally:
        engine, Session = database(url)
        with Session.begin() as db:
            db.execute(delete(PlanRecoveryAudit).where(PlanRecoveryAudit.conversation_id == cid))
            db.execute(delete(PlanToolDispatch).where(PlanToolDispatch.conversation_id == cid))
            db.execute(delete(PlanExecutionClaim).where(PlanExecutionClaim.conversation_id == cid))
            db.execute(delete(ConversationPlan).where(ConversationPlan.conversation_id == cid))
            db.execute(delete(Turn).where(Turn.conversation_id == cid))
            db.execute(delete(Message).where(Message.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
        engine.dispose()
