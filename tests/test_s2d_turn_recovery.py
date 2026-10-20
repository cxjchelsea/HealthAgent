"""Crash recovery must never resurrect, replay or mark tools as succeeded."""
from datetime import datetime, timedelta, timezone

from services.db import Conversation, Message, Turn, database
from services.migrate import migrate
from services.recover_turns import recover_abandoned


def test_interrupted_turn_recovery_requires_explicit_apply(tmp_path):
    url = f"sqlite:///{tmp_path / 'recovery.db'}"
    migrate(url)
    engine, Session = database(url)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    with Session.begin() as db:
        db.add(Conversation(id="c1", owner_id="u"))
        db.add(Message(id="m1", conversation_id="c1", role="assistant",
                       content="", status="pending", created_at=old))
        db.add(Turn(id="t1", conversation_id="c1", request_id="recover-001",
                    assistant_message_id="m1", state="running"))
    assert recover_abandoned(url, minimum_age_minutes=10) == ["t1"]
    with Session() as db:
        assert db.get(Turn, "t1").state == "running"
    assert recover_abandoned(url, minimum_age_minutes=10, apply=True) == ["t1"]
    with Session() as db:
        assert db.get(Turn, "t1").state == "interrupted"
        assert db.get(Message, "m1").status == "interrupted"
    assert recover_abandoned(url, minimum_age_minutes=10, apply=True) == []
    engine.dispose()
