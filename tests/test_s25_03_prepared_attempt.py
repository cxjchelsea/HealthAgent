"""Durable attempt must exist before tool execution; unknown outcomes never auto-replay."""
import pytest
from services.db import Conversation, Message, Turn, AgentAttempt, database
from services.migrate import migrate
from services.evidence_store import AttemptEvidenceStore, make_attempt


def _setup(tmp_path):
    url = f"sqlite:///{tmp_path / 'journal.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="cid", owner_id="owner"))
        db.add(Message(id="msg", conversation_id="cid", role="assistant",
                       content="", status="pending"))
        db.add(Turn(id="turn", conversation_id="cid", request_id="journal-request",
                    assistant_message_id="msg", state="running"))
    return engine, Session


def test_begin_is_independent_of_turn_finalization(tmp_path):
    engine, Session = _setup(tmp_path)
    store = AttemptEvidenceStore("cid", "turn")
    prepared = make_attempt("run", "create_task", {"title": "buy milk"},
                            {"ok": False, "error": "unconfirmed"})
    store.begin(Session, prepared)
    with Session() as db:
        row = db.get(AgentAttempt, prepared["attempt_id"])
        assert row.status == "prepared"
        assert row.result_projection is None
    assert store.unresolved(Session)[0]["status"] == "unknown_outcome"
    assert store.unresolved(Session)[0]["tool"] == "create_task"
    with Session() as db:
        assert db.get(Turn, "turn").state == "running"
    engine.dispose()


def test_finish_terminal_observation_cannot_be_overwritten(tmp_path):
    engine, Session = _setup(tmp_path)
    store = AttemptEvidenceStore("cid", "turn")
    attempt = make_attempt("run", "calculate", {"expression": "8*8"},
                           {"ok": True, "value": 64}, 1)
    store.begin(Session, attempt)
    store.finish(Session, attempt)
    assert store.unresolved(Session) == []
    with Session() as db:
        row = db.get(AgentAttempt, attempt["attempt_id"])
        assert row.status == "succeeded"
        assert '"value": 64' in row.result_projection
    with pytest.raises(RuntimeError, match="attempt_outcome_conflict"):
        store.finish(Session, attempt)
    engine.dispose()


def test_existing_finalized_attempt_not_duplicated_in_turn_commit(tmp_path):
    engine, Session = _setup(tmp_path)
    store = AttemptEvidenceStore("cid", "turn")
    attempt = make_attempt("run", "calculate", {"expression": "8*8"},
                           {"ok": True, "value": 64}, 1)
    store.begin(Session, attempt)
    store.finish(Session, attempt)
    with Session.begin() as db:
        store.save_in_session(db, [attempt])
        db.get(Turn, "turn").state = "completed"
    with Session() as db:
        assert db.get(AgentAttempt, attempt["attempt_id"]).status == "succeeded"
    engine.dispose()


def test_succeeded_write_with_failed_turn_requires_reconciliation(tmp_path):
    engine, Session = _setup(tmp_path)
    store = AttemptEvidenceStore("cid", "turn")
    attempt = make_attempt("run", "create_task", {"title": "already-created"},
                           {"ok": True, "id": "item1"})
    store.begin(Session, attempt)
    store.finish(Session, attempt)
    with Session.begin() as db:
        db.get(Turn, "turn").state = "provider_error"
    assert store.has_unresolved_writes(Session)
    with Session.begin() as db:
        db.get(Turn, "turn").state = "completed"
    assert not store.has_unresolved_writes(Session)
    engine.dispose()


def test_unresolved_write_fence_not_limited_by_first_100_records(tmp_path):
    engine, Session = _setup(tmp_path)
    store = AttemptEvidenceStore("cid", "turn")
    from services.db import AgentAttempt
    with Session.begin() as db:
        for i in range(110):
            db.add(AgentAttempt(
                id=f"read-{i}", conversation_id="cid", turn_id="turn",
                run_id="run", step_id=None, tool_name="calculate",
                arguments_digest="a" * 64, status="prepared"))
        db.add(AgentAttempt(
            id="write-last", conversation_id="cid", turn_id="turn",
            run_id="run", step_id=None, tool_name="save_memory",
            arguments_digest="b" * 64, status="prepared"))
    assert len(store.unresolved(Session)) == 100
    assert store.has_unresolved_writes(Session)
    engine.dispose()
