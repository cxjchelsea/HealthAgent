"""Phase4-A readback verifier contract: synthetic DB fixtures, no model/network."""
import sqlite3

import pytest

from evals.primary_wp_c_phase4_a_physical import audit, rows

CID = "synthetic-p4-conversation"
ORIGIN = "origin-turn"
SECOND = "reply-turn"
DIGEST = "a" * 64


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE turns (
            id TEXT, conversation_id TEXT, request_id TEXT, state TEXT);
        CREATE TABLE primary_route_evidence (
            turn_id TEXT, conversation_id TEXT, proposal_id TEXT,
            schema_version TEXT, route_kind TEXT, semantic_attempts INTEGER,
            preturn_snapshot_digest TEXT, disposition TEXT, admission_reason TEXT);
        CREATE TABLE pending_write_authorities (
            id TEXT, conversation_id TEXT, origin_turn_id TEXT,
            tool_name TEXT, status TEXT, consumed_turn_id TEXT);
        CREATE TABLE conversation_tasks (id TEXT, conversation_id TEXT, title TEXT);
        CREATE TABLE agent_attempts (
            id TEXT, conversation_id TEXT, turn_id TEXT,
            tool_name TEXT, status TEXT);
    """)
    yield conn
    conn.close()


def populate(db, group):
    target = ("clarify_response" if group != "cancel" else "respond")
    admission = ("primary_v1_pending_task_authorized" if group == "task"
                 else "primary_v1_pending_read_only" if group == "mcp"
                 else "primary_v1_answer_only")
    for tid, state, route, disposition, reason in [
        (ORIGIN, "partial", "clarify", "clarify", "primary_v1_clarification_required"),
        (SECOND, "completed", target, "allow", admission),
    ]:
        db.execute("INSERT INTO turns VALUES (?,?,?,?)",
                   (tid, CID, tid, state))
        db.execute("INSERT INTO primary_route_evidence VALUES (?,?,?,?,?,?,?,?,?)",
                   (tid, CID, "proposal-" + tid, "primary-route-v1",
                    route, 1, DIGEST, disposition, reason))
    if group in {"task", "cancel"}:
        db.execute("INSERT INTO pending_write_authorities VALUES (?,?,?,?,?,?)",
                   ("permit", CID, ORIGIN, "create_task",
                    "consumed" if group == "task" else "revoked",
                    SECOND if group == "task" else None))
    if group == "task":
        db.execute("INSERT INTO conversation_tasks VALUES (?,?,?)",
                   ("task", CID, "synthetic title"))
        db.execute("INSERT INTO agent_attempts VALUES (?,?,?,?,?)",
                   ("attempt", CID, SECOND, "create_task", "succeeded"))
    if group == "mcp":
        db.execute("INSERT INTO agent_attempts VALUES (?,?,?,?,?)",
                   ("attempt", CID, SECOND, "mcp_reference_lookup", "succeeded"))
    db.commit()


@pytest.mark.parametrize("group", ["task", "cancel", "mcp"])
def test_db_readback_accepts_exactly_matching_rows(db, group):
    populate(db, group)
    result = audit(db, CID, group, {"origin": ORIGIN, "answer": SECOND,
                                    "replay": None})
    assert result["verdict"] == "PASS"
    assert result["evidence_ok"] and result["effect_ok"]
    assert result["primary_evidence_count"] == 2


@pytest.mark.parametrize("group", ["task", "cancel", "mcp"])
def test_extra_task_or_fake_receipt_fails_readback(db, group):
    populate(db, group)
    db.execute("INSERT INTO conversation_tasks VALUES (?,?,?)",
               ("unexpected", CID, "unexpected"))
    db.commit()
    assert audit(db, CID, group, {"origin": ORIGIN, "answer": SECOND})["verdict"] == "FAIL"


def test_replay_creating_an_extra_turn_is_not_accepted(db):
    populate(db, "task")
    db.execute("INSERT INTO turns VALUES (?,?,?,?)",
               ("replayed", CID, "duplicate", "completed"))
    db.commit()
    assert audit(db, CID, "task", {"origin": ORIGIN, "answer": SECOND,
                                    "replay": None})["verdict"] == "FAIL"


def test_invalid_or_missing_primary_evidence_is_not_accepted(db):
    populate(db, "cancel")
    db.execute("UPDATE primary_route_evidence SET semantic_attempts = 2 "
               "WHERE turn_id = ?", (SECOND,))
    db.commit()
    assert audit(db, CID, "cancel", {"origin": ORIGIN, "withdraw": SECOND})["verdict"] == "FAIL"


def test_withdrawn_authority_cannot_be_counted_as_consumed(db):
    populate(db, "cancel")
    db.execute("UPDATE pending_write_authorities SET status='consumed', "
               "consumed_turn_id=? WHERE id='permit'", (SECOND,))
    db.commit()
    assert audit(db, CID, "cancel", {"origin": ORIGIN, "withdraw": SECOND})["verdict"] == "FAIL"


def test_audit_fixed_table_whitelist(db):
    with pytest.raises(ValueError, match="unknown_audit_table"):
        rows(db, "sqlite_master", CID)
