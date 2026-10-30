"""S25-03 state change ledger: history, rollback, revision invalidation."""
import pytest
from services.db import Conversation, database
from services.migrate import migrate
from services.task_state import AgentTaskStateStore


def test_transition_history_and_superseded_step(tmp_path):
    url = f"sqlite:///{tmp_path / 'transition.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="owner"))
    store = AgentTaskStateStore("c1")
    first = {"revision": 1, "steps": [
        {"id": 1, "status": "completed", "evidence_tool": "calculate"},
        {"id": 2, "status": "pending"}]}
    with Session.begin() as db:
        task_id, rev = store.save_in_session(db, first, "partial", None)
    assert rev == 1
    initial = store.history(Session)
    assert [(x["entity_type"], x["to_state"]) for x in initial] == [
        ("task", "partial"), ("step", "completed"), ("step", "pending")]
    revised = {"revision": 2, "steps": [
        {"id": 2, "status": "completed", "evidence_tool": "calculate"},
        {"id": 3, "status": "pending"}]}
    with Session.begin() as db:
        same, revision = store.save_in_session(db, revised, "partial", 1)
    assert task_id == same and revision == 2
    history = store.history(Session)
    assert any(x["step_id"] == 1 and x["from_state"] == "completed"
               and x["to_state"] == "superseded" for x in history)
    assert any(x["step_id"] == 2 and x["to_state"] == "completed" for x in history)
    assert any(x["step_id"] == 3 and x["to_state"] == "pending" for x in history)
    assert store.load(Session)["revision"] == 2
    engine.dispose()


def test_transitions_rollback_with_state_cas(tmp_path):
    url = f"sqlite:///{tmp_path / 'rollback.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="owner"))
    store = AgentTaskStateStore("c1")
    first = {"revision": 1, "steps": [{"id": 1, "status": "pending"}]}
    with Session.begin() as db:
        store.save_in_session(db, first, "partial", None)
    before = store.history(Session)
    with pytest.raises(RuntimeError, match="rollback"):
        with Session.begin() as db:
            store.save_in_session(db, {"revision": 1, "steps": [
                {"id": 1, "status": "completed", "evidence_tool": "calculate"}]},
                "completed", 1)
            db.flush()
            raise RuntimeError("rollback")
    assert store.load(Session)["revision"] == 1
    assert store.history(Session) == before
    with pytest.raises(RuntimeError, match="stale_task_revision"):
        with Session.begin() as db:
            store.save_in_session(db, first, "partial", 0)
    engine.dispose()
