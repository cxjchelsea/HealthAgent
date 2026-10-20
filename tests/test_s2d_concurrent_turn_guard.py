"""Database-enforced per-conversation running-turn admission."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from services.db import Conversation, Turn, database
from services.migrate import migrate


def test_running_turn_unique_per_conversation(tmp_path):
    url = "sqlite:///" + str(tmp_path / "guard.sqlite3")
    migrate(url)
    engine, Session = database(url)
    try:
        with Session.begin() as db:
            db.add(Conversation(id="c1", owner_id="owner"))
            db.add(Conversation(id="c2", owner_id="owner"))
        with Session.begin() as db:
            db.add(Turn(id=str(uuid.uuid4()), conversation_id="c1", request_id="req-00001",
                        state="running"))
        with pytest.raises(IntegrityError):
            with Session.begin() as db:
                db.add(Turn(id=str(uuid.uuid4()), conversation_id="c1",
                            request_id="req-00002", state="running"))
        with Session.begin() as db:
            db.add(Turn(id=str(uuid.uuid4()), conversation_id="c2",
                        request_id="req-00003", state="running"))
        with Session.begin() as db:
            first = db.query(Turn).filter_by(conversation_id="c1").one()
            first.state = "completed"
        with Session.begin() as db:
            db.add(Turn(id=str(uuid.uuid4()), conversation_id="c1",
                        request_id="req-00004", state="running"))
    finally:
        engine.dispose()
