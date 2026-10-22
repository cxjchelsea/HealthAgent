"""Failure-state projection and causal step transitions."""
from services.db import Conversation, database
from services.migrate import migrate
from services.task_state import AgentTaskStateStore

def test_failed_turn_preserves_checkpoint_and_links_successful_attempt(tmp_path):
    url = f"sqlite:///{tmp_path/'failure.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="test"))
    store = AgentTaskStateStore("c1")
    plan = {"revision": 1, "steps": [
        {"id": 1, "status": "pending", "expected_tool": "calculate",
         "expected_arguments": {"expression": "8*8"}},
        {"id": 2, "status": "pending"}]}
    with Session.begin() as db:
        store.save_in_session(db, plan, "partial", None)
    done = {"revision": 1, "steps": [
        {"id": 1, "status": "completed", "evidence_tool": "calculate"},
        {"id": 2, "status": "pending"}]}
    with Session.begin() as db:
        store.save_in_session(db, done, "partial", 1, [{
            "step_id": 1, "attempt_id": "attempt-real-1", "status": "succeeded"}])
    events = store.history(Session)
    assert any(e["step_id"] == 1 and e["attempt_id"] == "attempt-real-1"
               and e["to_state"] == "completed" for e in events)
    with Session.begin() as db:
        store.save_in_session(db, done, "provider_error", 2)
    current = store.load(Session)
    assert current["state"] == "failed"
    assert current["steps"][0]["status"] == "completed"
    assert current["steps"][1]["status"] == "pending"
    assert any(e["entity_type"] == "task" and e["to_state"] == "failed"
               for e in store.history(Session))
    engine.dispose()
