"""S2-D conversation persistence, ownership, and recovery checkpoint tests."""
import asyncio
from agent.decision import Decision
from agent.loop import AgentLoop
from agent.planning import PlanLedger
from services.db import database, Base, Conversation, ConversationPlan
from services.plan_store import ConversationPlanStore


def test_checkpoint_roundtrip_and_revision(tmp_path):
    engine, Session = database(f"sqlite:///{tmp_path/'checkpoint.db'}")
    Base.metadata.create_all(engine)
    with Session.begin() as db:
        db.add(Conversation(id="c-1", owner_id="alice"))
    store = ConversationPlanStore(Session, "c-1")
    ledger = PlanLedger()
    assert ledger.propose([{"goal": "calculate", "expected_tool": "calculate",
                            "expected_arguments": {"expression": "8*8"}}])["ok"]
    store.save(ledger.checkpoint())
    hydrated = PlanLedger.restore(store.load())
    assert hydrated.revision == 1
    hydrated.observe("calculate", {"ok": True, "value": 64}, {"expression": "8*8"})
    assert hydrated.steps[0]["status"] == "completed"
    store.save(hydrated.checkpoint())
    assert ConversationPlanStore(Session, "c-1").load()["steps"][0]["status"] == "completed"
    with Session() as db:
        assert db.get(ConversationPlan, "c-1") is not None
    engine.dispose()


class ContinuePlan:
    async def decide(self, messages, tools):
        assert "Trusted checkpoint: revision 1" in messages[0]["content"]
        return Decision("", [{"id": "tool", "name": "calculate",
                              "arguments": {"expression": "8*8"}}])


class FinishPlan:
    async def decide(self, messages, tools):
        return Decision("全部完成", [])


def test_resume_reuses_verified_plan_and_tools():
    ledger = PlanLedger()
    ledger.propose([{"goal": "calculate", "expected_tool": "calculate",
                     "expected_arguments": {"expression": "8*8"}}])
    class ContinueThenFinish:
        def __init__(self):
            self.n = 0
        async def decide(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return await ContinuePlan().decide(messages, tools)
            return Decision("完成", [])
    response = asyncio.run(AgentLoop(ContinueThenFinish()).run(
        "继续上一步", plan_checkpoint=ledger.checkpoint()))
    assert response["status"] == "completed"
    assert response["plan"][0]["status"] == "completed"


def test_invalid_checkpoint_fails_closed():
    try:
        PlanLedger.restore({"revision": 1, "steps": [{"id": 1, "goal": "fake",
                            "status": "completed"}]})
    except ValueError:
        pass
    else:
        raise AssertionError("unchecked completed step")
