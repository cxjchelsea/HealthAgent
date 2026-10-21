"""S2 H-series: duplicate request, atomic finalization and operator recovery."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app
from services.db import Conversation, Message, Turn, database
from services.plan_store import ConversationPlanStore
from services.recover_turns import recover_abandoned


class Finished:
    async def decide(self, messages, tools):
        return Decision("已完成", [])


class OneStep:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "p", "name": "propose_plan",
                "arguments": {"steps": [{"goal": "calculate",
                  "expected_tool": "calculate",
                  "expected_arguments": {"expression": "8*8"}}]}}])
        if self.calls == 2:
            return Decision("", [{"id": "c", "name": "calculate",
                                  "arguments": {"expression": "8*8"}}])
        return Decision("64", [])


def test_h02_duplicate_id_does_not_create_second_turn(tmp_path):
    url = f"sqlite:///{tmp_path/'h02.db'}"
    with TestClient(create_app(Settings(database_url=url, model_provider="fake"),
                               agent_decider=Finished())) as client:
        cid = client.post("/conversations").json()["id"]
        path = f"/conversations/{cid}/agent/messages"
        payload = {"content": "Hello", "client_request_id": "h02-request-0001"}
        first = client.post(path, json=payload)
        assert first.status_code == 200
        second = client.post(path, json=payload)
        assert second.status_code == 409
        assert second.json()["detail"] == "duplicate_request_id"
        engine, Session = database(url)
        with Session() as db:
            assert len(db.scalars(select(Turn).where(Turn.conversation_id == cid)).all()) == 1
            assert len(db.scalars(select(Message).where(Message.conversation_id == cid)).all()) == 2
        engine.dispose()


def test_h06_checkpoint_commit_failure_never_leaks_success(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path/'h06.db'}"
    with TestClient(create_app(Settings(database_url=url, model_provider="fake"),
                               agent_decider=OneStep())) as client:
        cid = client.post("/conversations").json()["id"]
        def reject_snapshot(self, db, snapshot):
            raise RuntimeError("test_injected_checkpoint_commit_failure")
        monkeypatch.setattr(ConversationPlanStore, "save_in_session", reject_snapshot)
        response = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": "Create and execute a plan",
                  "client_request_id": "h06-request-0001"})
        assert response.status_code == 503
        assert response.json()["detail"] == "turn_finalization_failed"
        engine, Session = database(url)
        with Session() as db:
            turn = db.scalar(select(Turn).where(Turn.conversation_id == cid))
            assistant = db.get(Message, turn.assistant_message_id)
            assert turn.state == "running"
            assert assistant.status == "pending"
            assert assistant.content == ""
            assert ConversationPlanStore(Session, cid).load() is None
        engine.dispose()


def test_h07_dry_run_then_manual_recovery_is_non_replaying(tmp_path):
    url = f"sqlite:///{tmp_path/'h07.db'}"
    with TestClient(create_app(Settings(database_url=url, model_provider="fake"),
                               agent_decider=Finished())) as client:
        cid = client.post("/conversations").json()["id"]
    engine, Session = database(url)
    with Session.begin() as db:
        msg = Message(conversation_id=cid, role="assistant",
                      content="", status="pending",
                      created_at=datetime.now(timezone.utc)-timedelta(minutes=25))
        db.add(msg)
        db.flush()
        turn = Turn(conversation_id=cid, request_id="h07-abandoned-0001",
                    assistant_message_id=msg.id, state="running")
        db.add(turn)
        db.flush()
        turn_id, msg_id = turn.id, msg.id
    engine.dispose()
    assert turn_id in recover_abandoned(url, minimum_age_minutes=10, apply=False)
    engine, Session = database(url)
    with Session() as db:
        assert db.get(Turn, turn_id).state == "running"
        assert db.get(Message, msg_id).status == "pending"
    engine.dispose()
    assert turn_id in recover_abandoned(url, minimum_age_minutes=10, apply=True)
    engine, Session = database(url)
    with Session() as db:
        assert db.get(Turn, turn_id).state == "interrupted"
        assert db.get(Message, msg_id).status == "interrupted"
        assert db.get(Message, msg_id).content == ""
    engine.dispose()
    assert recover_abandoned(url, minimum_age_minutes=10, apply=True) == []
