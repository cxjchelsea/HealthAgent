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
    assert any(e["step_id"] == 1 and e["attempt_id"] is None
               and e["reason_code"] == "legacy_completion_unlinked"
               for e in events)
    with Session.begin() as db:
        store.save_in_session(db, done, "provider_error", 2)
    current = store.load(Session)
    assert current["state"] == "failed"
    assert current["steps"][0]["status"] == "completed"
    assert current["steps"][1]["status"] == "pending"
    assert any(e["entity_type"] == "task" and e["to_state"] == "failed"
               for e in store.history(Session))
    engine.dispose()


def test_step_completion_links_only_real_matching_persisted_attempt(tmp_path):
    from services.db import Message, Turn
    from services.evidence_store import AttemptEvidenceStore, make_attempt
    url = f"sqlite:///{tmp_path/'verified.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="test"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="completed"))
        db.add(Turn(id="t1", conversation_id="c1", request_id="request-verified-01",
                    assistant_message_id="m1", state="completed"))
    record = make_attempt("run", "calculate", {"expression": "8*8"},
                          {"ok": True, "value": 64}, step_id=1)
    journal = AttemptEvidenceStore("c1", "t1")
    journal.begin(Session, record)
    journal.finish(Session, record)
    from services.db import AgentAttempt
    with Session() as db:
        stored = db.get(AgentAttempt, record["attempt_id"])
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.tool_name == "calculate"
        assert stored.step_id == 1
        assert stored.run_id == record["run_id"]
        assert stored.arguments_digest == record["arguments_digest"]
        plan = {"revision": 1, "steps": [{
        "id": 1, "status": "completed", "evidence_tool": "calculate",
        "expected_tool": "calculate", "expected_arguments": {"expression": "8*8"}}]}
    with Session.begin() as db:
        AgentTaskStateStore("c1").save_in_session(
            db, plan, "completed", None, [record], current_turn_id="t1")
    linked = AgentTaskStateStore("c1").history(Session)
    assert any(e["step_id"] == 1 and e["attempt_id"] == record["attempt_id"]
               for e in linked), linked
    engine.dispose()


def test_mismatched_arguments_never_link_attempt(tmp_path):
    from services.db import Message, Turn
    from services.evidence_store import AttemptEvidenceStore, make_attempt
    url = f"sqlite:///{tmp_path/'mismatch.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="test"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="completed"))
        db.add(Turn(id="t1", conversation_id="c1", request_id="request-mismatch-01",
                    assistant_message_id="m1", state="completed"))
    attempt = make_attempt("run", "calculate", {"expression": "8*8"},
                           {"ok": True, "value": 64}, step_id=1)
    journal = AttemptEvidenceStore("c1", "t1")
    journal.begin(Session, attempt)
    journal.finish(Session, attempt)
    changed_plan = {"revision": 2, "steps": [{
        "id": 1, "status": "completed", "evidence_tool": "calculate",
        "expected_tool": "calculate", "expected_arguments": {"expression": "9*9"}}]}
    with Session.begin() as db:
        AgentTaskStateStore("c1").save_in_session(
            db, changed_plan, "completed", None, [attempt], current_turn_id="t1")
    assert all(e["attempt_id"] is None for e in
               AgentTaskStateStore("c1").history(Session))
    engine.dispose()
