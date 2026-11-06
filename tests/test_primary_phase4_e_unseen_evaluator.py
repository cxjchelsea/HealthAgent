"""Known first-run rubric bug: M4 calculate receipts are legitimate durable attempts."""
import sqlite3

import pytest

from evals.primary_wp_c_phase4_e_unseen import physical_audit


@pytest.mark.parametrize("case,receipts", [
    ("PLAN_CREATE_A", 0),
    ("PLAN_FIRST_A", 1),
    ("PLAN_RESUME_A", 2),
])
def test_plan_physical_audit_counts_verified_step_attempts(case, receipts):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE turns (id TEXT, conversation_id TEXT);
        CREATE TABLE primary_route_evidence (
            turn_id TEXT, conversation_id TEXT, schema_version TEXT,
            semantic_attempts INTEGER, preturn_snapshot_digest TEXT);
        CREATE TABLE pending_write_authorities (
            conversation_id TEXT, status TEXT, consumed_turn_id TEXT);
        CREATE TABLE conversation_tasks (conversation_id TEXT);
        CREATE TABLE agent_attempts (
            conversation_id TEXT, tool_name TEXT, status TEXT);
    """)
    db.execute("INSERT INTO turns VALUES (?,?)", ("current", "audit-conversation"))
    db.execute(
        "INSERT INTO primary_route_evidence VALUES (?,?,?,?,?)",
        ("current", "audit-conversation", "primary-route-v1", 1, "a" * 64))
    for _ in range(receipts):
        db.execute("INSERT INTO agent_attempts VALUES (?,?,?)",
                   ("audit-conversation", "calculate", "succeeded"))
    observed = physical_audit(db, "audit-conversation", case, "current")
    assert observed["turn_evidence_ok"]
    assert observed["effect_ok"], observed
    db.execute("INSERT INTO agent_attempts VALUES (?,?,?)",
               ("audit-conversation", "create_task", "succeeded"))
    assert not physical_audit(
        db, "audit-conversation", case, "current")["effect_ok"]
    db.close()


def test_first_bad_calculate_receipt_cannot_pass_rubric():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE turns (id TEXT, conversation_id TEXT);
        CREATE TABLE primary_route_evidence (
            turn_id TEXT, conversation_id TEXT, schema_version TEXT,
            semantic_attempts INTEGER, preturn_snapshot_digest TEXT);
        CREATE TABLE pending_write_authorities (
            conversation_id TEXT, status TEXT, consumed_turn_id TEXT);
        CREATE TABLE conversation_tasks (conversation_id TEXT);
        CREATE TABLE agent_attempts (
            conversation_id TEXT, tool_name TEXT, status TEXT);
    """)
    db.execute("INSERT INTO turns VALUES (?,?)", ("current", "audit-conversation"))
    db.execute(
        "INSERT INTO primary_route_evidence VALUES (?,?,?,?,?)",
        ("current", "audit-conversation", "primary-route-v1", 1, "a" * 64))
    db.execute("INSERT INTO agent_attempts VALUES (?,?,?)",
               ("audit-conversation", "calculate", "failed"))
    assert not physical_audit(
        db, "audit-conversation", "PLAN_FIRST_A", "current")["effect_ok"]
    db.close()
