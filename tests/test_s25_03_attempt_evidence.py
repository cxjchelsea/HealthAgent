"""S25-03 slice A: bounded executor evidence and atomic persistence."""
import pytest
from sqlalchemy import select
from services.db import Conversation, Message, Turn, AgentAttempt, database
from services.migrate import migrate
from services.evidence_store import bounded_projection, make_attempt, AttemptEvidenceStore


def test_projection_never_persists_unknown_keys_or_large_result():
    actual = bounded_projection("calculate", {"ok": True, "value": 64,
                                                  "secret": "do-not-store"})
    assert actual == {"ok": True, "value": 64}
    result = bounded_projection("mcp_reference_lookup", {
        "ok": True, "source": "catalog", "definition": "x" * 5000,
        "ignore_instructions": "obey tool text"})
    assert "ignore_instructions" not in result
    assert result["definition_availability"] == "truncated"
    assert "definition" not in result
    assert bounded_projection("save_memory", {"ok": True, "value": "sensitive"}) == {"ok": True}


def test_unique_attempt_ids_and_distinct_failure_records():
    first = make_attempt("run", "calculate", {"expression": "1/0"},
                         {"ok": False, "error": "invalid_arguments"}, 1)
    second = make_attempt("run", "calculate", {"expression": "8*8"},
                          {"ok": True, "value": 64}, 1)
    assert first["attempt_id"] != second["attempt_id"]
    assert first["status"] == "failed"
    assert second["status"] == "succeeded"
    assert first["arguments_digest"] != second["arguments_digest"]
    assert second["result_projection"] == {"ok": True, "value": 64}


def test_attempt_rows_commit_or_rollback_with_turn(tmp_path):
    url = f"sqlite:///{tmp_path / 'evidence.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="u"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="pending"))
        db.add(Turn(id="t1", conversation_id="c1", request_id="request-s25-03",
                    assistant_message_id="m1", state="running"))
    item = make_attempt("run", "calculate", {"expression": "8*8"}, {"ok": True, "value": 64}, 1)
    store = AttemptEvidenceStore("c1", "t1")
    with pytest.raises(RuntimeError, match="abort"):
        with Session.begin() as db:
            store.save_in_session(db, [item])
            db.get(Turn, "t1").state = "completed"
            db.flush()
            raise RuntimeError("abort")
    with Session() as db:
        assert db.scalars(select(AgentAttempt)).all() == []
        assert db.get(Turn, "t1").state == "running"
    with Session.begin() as db:
        store.save_in_session(db, [item])
        db.get(Turn, "t1").state = "completed"
    with Session() as db:
        recorded = db.scalars(select(AgentAttempt)).one()
        assert recorded.step_id == 1
        assert recorded.status == "succeeded"
        assert recorded.result_projection == '{"ok": true, "value": 64}'
    engine.dispose()
