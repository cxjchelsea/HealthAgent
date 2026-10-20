"""Plan, reply and turn state must commit or roll back together."""
import pytest
from services.db import Conversation, Message, Turn, ConversationPlan, database
from services.migrate import migrate
from services.plan_store import ConversationPlanStore


def test_checkpoint_and_turn_finalization_rollback_together(tmp_path):
    url = f"sqlite:///{tmp_path / 'atomic.db'}"
    migrate(url)
    engine, Session = database(url)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="u"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="pending"))
        db.add(Turn(id="t1", conversation_id="c1", request_id="request-atomic-01",
                    assistant_message_id="m1", state="running"))
    plan_store = ConversationPlanStore(Session, "c1")
    with pytest.raises(RuntimeError, match="abort"):
        with Session.begin() as db:
            plan_store.save_in_session(db, {"revision": 1})
            response = db.get(Message, "m1")
            turn = db.get(Turn, "t1")
            response.content = "done"
            response.status = "completed"
            turn.state = "completed"
            db.flush()
            raise RuntimeError("abort")
    with Session() as db:
        assert db.get(ConversationPlan, "c1") is None
        assert db.get(Message, "m1").status == "pending"
        assert db.get(Turn, "t1").state == "running"
    with Session.begin() as db:
        plan_store.save_in_session(db, {"revision": 1})
        db.get(Message, "m1").status = "completed"
        db.get(Turn, "t1").state = "completed"
    assert plan_store.load() == {"revision": 1}
    engine.dispose()
