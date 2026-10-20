"""Stable plan-step identities across partial invalidation and durable restore."""
from agent.planning import PlanLedger
from services.db import Base, Conversation, database
from services.plan_store import ConversationPlanStore


def _bound(label, expression):
    return {"goal": label, "expected_tool": "calculate",
            "expected_arguments": {"expression": expression}}


def test_partial_invalidation_preserves_ids_and_completes_by_id():
    ledger = PlanLedger()
    assert ledger.propose([_bound("old", "8*8"),
                           _bound("keep", "7*7")])["ok"]
    ledger.observe("calculate", {"ok": True, "value": 64}, {"expression": "8*8"})
    ledger.observe("calculate", {"ok": True, "value": 49}, {"expression": "7*7"})
    assert ledger.revise(1, ["replacement"], "replace first",
                         invalidate_step_ids=[1])["ok"]
    assert [x["id"] for x in ledger.steps] == [2, 3]
    assert ledger.complete(1, "calculate",
                           {"ok": True, "name": "calculate"})["error"] == "unknown_step"
    assert ledger.complete(3, "calculate",
                           {"ok": True, "name": "calculate"})["ok"]
    assert [x["id"] for x in ledger.steps] == [2, 3]
    assert ledger.revise(2, ["another"], "replace replacement",
                         invalidate_step_ids=[3])["ok"]
    assert [x["id"] for x in ledger.steps] == [2, 4]
    assert ledger.next_step_id == 5


def test_partial_invalidation_persists_and_restores_sqlite(tmp_path):
    url = f"sqlite:///{tmp_path / 'stable_steps.db'}"
    engine, Session = database(url)
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        db.add(Conversation(id="conv-stable", owner_id="owner"))
    store = ConversationPlanStore(Session, "conv-stable")
    ledger = PlanLedger()
    ledger.propose([_bound("first", "8*8"), _bound("retain", "7*7")])
    ledger.observe("calculate", {"ok": True}, {"expression": "8*8"})
    ledger.observe("calculate", {"ok": True}, {"expression": "7*7"})
    assert ledger.revise(1, [_bound("new", "9*9")], "changed",
                         invalidate_step_ids=[1])["ok"]
    store.save(ledger.checkpoint())
    restored = PlanLedger.restore(store.load())
    assert [step["id"] for step in restored.steps] == [2, 3]
    assert restored.next_step_id == 4
    restored.observe("calculate", {"ok": True}, {"expression": "9*9"})
    assert restored.steps[1]["status"] == "completed"
    assert restored.revise(2, [_bound("next", "6*6")], "changed again",
                          invalidate_step_ids=[3])["ok"]
    assert [step["id"] for step in restored.steps] == [2, 4]
    store.save(restored.checkpoint())
    reread = PlanLedger.restore(store.load())
    assert [step["id"] for step in reread.steps] == [2, 4]
    assert reread.next_step_id == 5
    engine.dispose()


def test_legacy_checkpoint_fallback_and_duplicate_rejection():
    legacy = {"revision": 1, "steps": [
        {"id": 1, "goal": "first", "status": "pending"}]}
    restored = PlanLedger.restore(legacy)
    assert restored.next_step_id == 2
    malformed = {"revision": 2, "next_step_id": 4, "steps": [
        {"id": 2, "goal": "one", "status": "pending"},
        {"id": 2, "goal": "two", "status": "pending"}]}
    try:
        PlanLedger.restore(malformed)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate IDs must be rejected")


def test_oversized_revision_rejected_without_mutation():
    ledger = PlanLedger()
    ledger.propose([_bound("a", "8*8")])
    ledger.observe("calculate", {"ok": True}, {"expression": "8*8"})
    before = ledger.checkpoint()
    result = ledger.revise(1, ["new"] * 8, "too many preserved")
    assert result["error"] == "invalid_revision"
    assert ledger.checkpoint() == before
