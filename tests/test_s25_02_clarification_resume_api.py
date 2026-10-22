"""B03-F02: source-bound clarification continues the originally authorized task."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from agent.config import Settings
from agent.decision import Decision
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app
from services.db import ConversationTask, database


class MissingTitleDecider:
    async def decide(self, messages, tools):
        return Decision("", [{"id": "t", "name": "create_task",
                              "arguments": {"title": "示例任务"}}])


def fixture(monkeypatch, replies):
    calls = []
    async def fake_complete(self, *, messages, model, **kwargs):
        calls.append(messages)
        kind = replies[len(calls)-1]
        return json.dumps({
            "status": "understood", "goal": {
                "objective": "创建待办任务", "subgoals": [],
                "constraints": [], "success_criteria": []},
            "change": {"kind": kind, "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None},
            "schema_version": "s25-01-v1"}, ensure_ascii=False)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    return calls


def test_b03_question_followed_by_title_creates_one_task(tmp_path, monkeypatch):
    understanding_calls = fixture(monkeypatch, ["new_goal", "clarify_response"])
    async def policy(message, interpretation):
        return "execute"
    settings = Settings(database_url=f"sqlite:///{tmp_path/'b03.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=MissingTitleDecider(),
                               execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        def send(msg, request):
            return client.post(f"/conversations/{cid}/agent/messages",
                               json={"content": msg, "client_request_id": request})
        first = send("帮我创建一个任务。", "b03-turn-001")
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "partial"
        assert first.json()["termination_reason"] == "needs_input"
        second = send("做项目文档", "b03-turn-002")
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "completed"
        assert second.json()["termination_reason"] == "verified_clarification_execution"
        assert "做项目文档" in second.json()["answer"]
        assert len(understanding_calls) == 2
        assert "outstanding_clarification" in understanding_calls[1][-1]["content"]
        engine, Session = database(settings.database_url)
        with Session() as db:
            tasks = db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id == cid)).all()
            assert [task.title for task in tasks] == ["做项目文档"]
        engine.dispose()


def test_new_goal_does_not_resume_pending_write(tmp_path, monkeypatch):
    fixture(monkeypatch, ["new_goal", "new_goal"])
    async def policy(message, interpretation):
        return "respond_only" if message == "介绍 Python" else "execute"
    class Diverter:
        def __init__(self): self.calls=0
        async def decide(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return Decision("", [{"id": "t", "name": "create_task",
                                     "arguments": {"title": "示例任务"}}])
            return Decision("Python 是一种编程语言。", [])
    d=Diverter()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'newgoal.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=d, execution_policy=policy)) as client:
        cid=client.post("/conversations").json()["id"]
        route=f"/conversations/{cid}/agent/messages"
        client.post(route,json={"content":"创建一个任务","client_request_id":"drift-001"})
        second=client.post(route,json={"content":"介绍 Python","client_request_id":"drift-002"})
        assert second.status_code == 200
        assert second.json()["status"] == "completed"
        assert second.json()["termination_reason"] != "verified_clarification_execution"
        engine, Session = database(settings.database_url)
        with Session() as db:
            assert not db.scalars(select(ConversationTask).where(
                ConversationTask.conversation_id==cid)).all()
        engine.dispose()
