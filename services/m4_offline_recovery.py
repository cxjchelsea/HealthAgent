"""OFFLINE, one-way quarantine of an uncertain M4 plan.

Only run with all API/worker processes stopped, after independent external
effect investigation. This cancels the old plan and permanently records an
attestation; it NEVER dispatches or retries any tool, and it cannot prove
an external operation happened exactly once.
"""
import argparse
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from agent.planning import PlanLedger
from services.plan_dispatch import UNRESOLVED_DISPATCH_STATUSES
from services.db import (ConversationPlan, PlanExecutionClaim, PlanToolDispatch,
                         PlanRecoveryAudit, Turn, Message, database)


class RecoveryRefused(RuntimeError):
    pass


def quarantine(*, database_url, conversation_id, turn_id,
               snapshot_sha256, evidence_file, operator_id):
    if not operator_id or len(operator_id) > 128:
        raise RecoveryRefused("invalid_operator")
    evidence_bytes = Path(evidence_file).read_bytes()
    evidence = json.loads(evidence_bytes)
    if (evidence.get("decision") != "quarantine_without_replay"
            or evidence.get("conversation_id") != conversation_id
            or evidence.get("turn_id") != turn_id
            or evidence.get("operator_id") != operator_id
            or evidence.get("external_effect_investigated") is not True
            or evidence.get("all_workers_stopped") is not True
            or not isinstance(evidence.get("investigation_note"), str)
            or len(evidence["investigation_note"].strip()) < 12):
        raise RecoveryRefused("incomplete_operator_attestation")
    engine, Session = database(database_url)
    try:
        with Session.begin() as db:
            plan = db.execute(select(ConversationPlan).where(
                ConversationPlan.conversation_id == conversation_id
            ).with_for_update()).scalar_one_or_none()
            if plan is None:
                raise RecoveryRefused("plan_missing")
            actual_hash = hashlib.sha256(plan.snapshot.encode("utf-8")).hexdigest()
            if actual_hash != snapshot_sha256:
                raise RecoveryRefused("plan_snapshot_changed")
            turn = db.get(Turn, turn_id)
            if (turn is None or turn.conversation_id != conversation_id
                    or turn.state not in {"running", "provider_error"}):
                raise RecoveryRefused("turn_not_recoverable")
            claim = db.get(PlanExecutionClaim, conversation_id)
            if claim is not None and claim.turn_id != turn_id:
                raise RecoveryRefused("another_turn_owns_claim")
            running = list(db.scalars(select(Turn).where(
                Turn.conversation_id == conversation_id,
                Turn.state == "running")))
            unknown = list(db.scalars(select(Turn).where(
                Turn.conversation_id == conversation_id,
                Turn.failure_code == "plan_execution_outcome_unknown")))
            if any(row.id != turn_id for row in running + unknown):
                raise RecoveryRefused("other_unresolved_turn")
            dispatches = list(db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == conversation_id,
                PlanToolDispatch.status.in_(UNRESOLVED_DISPATCH_STATUSES)).with_for_update()))
            if any(row.turn_id != turn_id for row in dispatches):
                raise RecoveryRefused("other_unresolved_dispatch")
            if not (claim or dispatches or turn.failure_code == "plan_execution_outcome_unknown"):
                raise RecoveryRefused("no_uncertain_execution_to_quarantine")
            # One transaction: quarantine old plan and dispatches; don't infer
            # success and don't copy any receipt into a new plan.
            for entry in dispatches:
                entry.status = "quarantined_no_replay"
            if claim:
                db.delete(claim)
            db.delete(plan)
            turn.state = "provider_error"
            turn.failure_code = "plan_execution_manually_quarantined"
            if turn.assistant_message_id:
                response = db.get(Message, turn.assistant_message_id)
                if response is not None:
                    response.status = "provider_error"
                    response.content = ""
                    turn.output_chars = 0
            db.add(PlanRecoveryAudit(
                conversation_id=conversation_id, turn_id=turn_id,
                operator_id=operator_id, decision="quarantine_without_replay",
                evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
                snapshot_sha256=actual_hash))
        return {"status": "quarantined_without_replay",
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "disposition": "old_plan_cancelled_no_tool_execution"}
    finally:
        engine.dispose()



def reconcile_verified_receipts(*, database_url, conversation_id, turn_id,
                                snapshot_sha256, evidence_file, operator_id):
    """Commit already-observed, read-only calculator outcomes without redispatch.

    Fails closed for any missing, failed, unsupported, or ambiguous receipt.
    The original Turn must be isolated with all workers stopped.
    """
    if not operator_id or len(operator_id) > 128:
        raise RecoveryRefused("invalid_operator")
    evidence_bytes = Path(evidence_file).read_bytes()
    evidence = json.loads(evidence_bytes)
    if (evidence.get("decision") != "reconcile_verified_receipts"
            or evidence.get("conversation_id") != conversation_id
            or evidence.get("turn_id") != turn_id
            or evidence.get("operator_id") != operator_id
            or evidence.get("all_workers_stopped") is not True
            or evidence.get("external_effect_investigated") is not True
            or not isinstance(evidence.get("investigation_note"), str)
            or len(evidence["investigation_note"].strip()) < 12):
        raise RecoveryRefused("incomplete_operator_attestation")
    engine, Session = database(database_url)
    try:
        with Session.begin() as db:
            plan = db.execute(select(ConversationPlan).where(
                ConversationPlan.conversation_id == conversation_id
            ).with_for_update()).scalar_one_or_none()
            if plan is None:
                raise RecoveryRefused("plan_missing")
            actual_hash = hashlib.sha256(plan.snapshot.encode("utf-8")).hexdigest()
            if actual_hash != snapshot_sha256:
                raise RecoveryRefused("plan_snapshot_changed")
            ledger = PlanLedger.restore(json.loads(plan.snapshot))
            turn = db.get(Turn, turn_id)
            if (turn is None or turn.conversation_id != conversation_id
                    or turn.state not in {"running", "provider_error"}):
                raise RecoveryRefused("turn_not_recoverable")
            claim = db.get(PlanExecutionClaim, conversation_id)
            if claim is not None and (claim.turn_id != turn_id
                                      or claim.snapshot != plan.snapshot):
                raise RecoveryRefused("claim_snapshot_mismatch")
            unresolved = list(db.scalars(select(PlanToolDispatch).where(
                PlanToolDispatch.conversation_id == conversation_id,
                PlanToolDispatch.status.in_(UNRESOLVED_DISPATCH_STATUSES)
            ).with_for_update()))
            if not unresolved or any(entry.turn_id != turn_id for entry in unresolved):
                raise RecoveryRefused("unbound_dispatch")
            other_turns = list(db.scalars(select(Turn).where(
                Turn.conversation_id == conversation_id,
                Turn.id != turn_id,
                ((Turn.state == "running") |
                 (Turn.failure_code == "plan_execution_outcome_unknown")))))
            if other_turns:
                raise RecoveryRefused("other_unresolved_turn")
            for entry in sorted(unresolved, key=lambda x: x.step_id):
                if entry.status != "observed" or entry.tool_name != "calculate":
                    raise RecoveryRefused("receipt_unverified")
                try:
                    arguments = json.loads(entry.arguments_json)
                    receipt = json.loads(entry.receipt_json)
                except (ValueError, TypeError) as exc:
                    raise RecoveryRefused("receipt_invalid") from exc
                step = next((x for x in ledger.steps if x["id"] == entry.step_id), None)
                if (step is None or step["status"] != "pending"
                        or step.get("expected_tool") != entry.tool_name
                        or step.get("expected_arguments") != arguments
                        or receipt.get("ok") is not True
                        or not isinstance(receipt.get("value"), str)):
                    raise RecoveryRefused("receipt_binding_mismatch")
                ledger.observe(entry.tool_name, receipt, arguments)
                if step["status"] != "completed":
                    raise RecoveryRefused("ambiguous_receipt_binding")
            plan.snapshot = json.dumps(ledger.checkpoint(), ensure_ascii=False)
            for entry in unresolved:
                entry.status = "checkpointed"
            if claim is not None:
                db.delete(claim)
            remaining = any(x["status"] == "pending" for x in ledger.steps)
            turn.state = "partial" if remaining else "completed"
            turn.failure_code = "plan_execution_receipt_reconciled"
            if turn.assistant_message_id:
                msg = db.get(Message, turn.assistant_message_id)
                if msg is not None:
                    msg.status = turn.state
                    msg.content = "已核对持久化计算回执并恢复计划状态；本次未重新调用工具。"
                    turn.output_chars = len(msg.content)
            db.add(PlanRecoveryAudit(
                conversation_id=conversation_id, turn_id=turn_id,
                operator_id=operator_id, decision="reconcile_verified_receipts",
                evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
                snapshot_sha256=actual_hash))
        return {"status": "receipts_reconciled_without_dispatch",
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "remaining_steps": remaining}
    finally:
        engine.dispose()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--database-url", required=True)
    p.add_argument("--conversation-id", required=True)
    p.add_argument("--turn-id", required=True)
    p.add_argument("--snapshot-sha256", required=True)
    p.add_argument("--evidence-file", required=True)
    p.add_argument("--operator-id", required=True)
    p.add_argument("--resolution", choices=("quarantine", "reconcile_receipts"),
                   default="quarantine")
    p.add_argument("--confirm-all-workers-stopped", action="store_true")
    args = p.parse_args()
    if not args.confirm_all_workers_stopped:
        p.error("All API/worker processes must be stopped; explicit confirmation required")
    try:
        action = (quarantine if args.resolution == "quarantine"
                  else reconcile_verified_receipts)
        print(json.dumps(action(
            database_url=args.database_url, conversation_id=args.conversation_id,
            turn_id=args.turn_id, snapshot_sha256=args.snapshot_sha256,
            evidence_file=args.evidence_file, operator_id=args.operator_id),
            ensure_ascii=False))
    except (RecoveryRefused, ValueError, OSError) as exc:
        p.error("Recovery denied: " + str(exc))


if __name__ == "__main__":
    main()
