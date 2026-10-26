"""B02/B03 host-level no-side-effect regression on S25-01 structured route."""
import json
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app
from services.db import ConversationTask, database
from sqlalchemy import select


class AlwaysCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments
        self.called = 0

    async def decide(self, messages, tools):
        self.called += 1
        return Decision("", [{"id": str(self.called), "name": self.name,
                              "arguments": self.arguments}])


def test_structured_api_never_creates_guessed_task(tmp_path, monkeypatch):
    async def fake_understand(self, *, messages, model):
        return json.dumps({
            "status": "understood",
            "goal": {"objective": "创建任务", "subgoals": [],
                     "constraints": [], "success_criteria": []},
            "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None},
            "schema_version": "s25-01-v1"}, ensure_ascii=False)

    async def policy(message, understanding):
        return "execute"

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_understand)
    decider = AlwaysCall("create_task", {"title": "示例任务"})
    settings = Settings(database_url=f"sqlite:///{tmp_path/'missing_title.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=decider,
                               execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": "帮我创建一个任务。", "client_request_id": "missing-title-test-01"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "partial"
        assert response.json()["termination_reason"] == "needs_input"
        engine, Session = database(settings.database_url)
        with Session() as db:
            assert db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all() == []
        engine.dispose()
