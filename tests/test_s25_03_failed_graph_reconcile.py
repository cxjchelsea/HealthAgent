"""Failure reconciliation may restore proven facts, never conjecture plan progress."""
from services.db import Conversation, Message, Turn, database
from services.migrate import migrate
from services.evidence_store import AttemptEvidenceStore, make_attempt


def test_failed_graph_reconciles_only_matching_successful_attempt(tmp_path):
    url = f"sqlite:///{tmp_path/'reconcile.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="cid", owner_id="user"))
        db.add(Message(id="assistant", conversation_id="cid", role="assistant",
                       content="", status="pending"))
        db.add(Turn(id="turn", conversation_id="cid", request_id="failed-graph",
                    assistant_message_id="assistant", state="running"))
    journal = AttemptEvidenceStore("cid", "turn")
    good = make_attempt("run", "calculate", {"expression": "8*8"},
                        {"ok": True, "value": 64}, 1)
    bad = make_attempt("run", "calculate", {"expression": "7*7"},
                       {"ok": True, "value": 49}, 2)
    pending = make_attempt("run", "create_task", {"title": "write"},
                           {"ok": False, "error": "unconfirmed"}, 3)
    for item in (good, bad, pending):
        journal.begin(Session, item)
    journal.finish(Session, good)
    journal.finish(Session, bad)
    checkpoint = {"revision": 1, "next_step_id": 4, "steps": [
        {"id": 1, "status": "pending", "goal": "calculate 8*8",
         "expected_tool": "calculate", "expected_arguments": {"expression": "8*8"}},
        {"id": 2, "status": "pending", "goal": "calculate 9*9",
         "expected_tool": "calculate", "expected_arguments": {"expression": "9*9"}},
        {"id": 3, "status": "pending", "goal": "create task",
         "expected_tool": "create_task", "expected_arguments": {"title": "write"}}]}
    recovered = journal.reconcile_failed_turn_checkpoint(Session, checkpoint)
    assert [s["status"] for s in recovered["steps"]] == ["completed", "pending", "pending"]
    assert recovered["steps"][0]["evidence_tool"] == "calculate"
    assert checkpoint["steps"][0]["status"] == "pending"  # Do not mutate original.
    assert journal.reconcile_failed_turn_checkpoint(Session, None) is None
    engine.dispose()
