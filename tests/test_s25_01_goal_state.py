"""S25-01 minimal goal-state roundtrip and real API history integration."""
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from agent.understanding import HTTPUnderstandingModel, UnderstandingResult
from apps.api.main import create_app
from services.db import database
from services.goal_store import ConversationGoalStore

def proposal(kind, objective, constraints=None):
    return json.dumps({
        "status": "understood",
        "goal": {"objective": objective, "subgoals": [],
                 "constraints": constraints or [], "success_criteria": []},
        "change": {"kind": kind, "target_goal_id": None, "delta": None},
        "clarification": {"required": False, "reason_code": "none", "question": None},
        "schema_version": "s25-01-v1"}, ensure_ascii=False)

class SimpleDecider:
    async def decide(self, messages, tools):
        return Decision("收到", [])

def test_goal_is_durable_and_visible_on_next_turn(tmp_path, monkeypatch):
    seen = []
    async def fake_complete(self, *, messages, model):
        data = json.loads(messages[-1]["content"])
        seen.append(data)
        if len(seen) == 1:
            return proposal("new_goal", "安排三项任务", [
                {"name": "limit", "value": "最多三项",
                 "source": "explicit", "scope": "goal"}])
        return proposal("continue", "安排三项任务")
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    settings = Settings(database_url=f"sqlite:///{tmp_path/'goal.db'}",
                        model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=SimpleDecider())) as client:
        cid = client.post("/conversations").json()["id"]
        path = f"/conversations/{cid}/agent/messages"
        for n, utterance in enumerate(["安排明天最多三项任务", "继续刚才的目标"], 1):
            response = client.post(path, json={
                "content": utterance, "client_request_id": f"goal-state-round-{n:03d}"})
            assert response.status_code == 200, response.text
        assert seen[1]["active_goal"]["objective"] == "安排三项任务"
        assert seen[1]["confirmed_constraints"][0]["value"] == "最多三项"
        engine, Session = database(settings.database_url)
        state = ConversationGoalStore(Session, cid).load()
        assert state["version"] == 2
        assert state["goal"]["objective"] == "安排三项任务"
        engine.dispose()

def test_goal_state_change_requires_consistent_version(tmp_path):
    from services.migrate import migrate
    url = f"sqlite:///{tmp_path/'cas.db'}"
    migrate(url)
    engine, Session = database(url)
    from services.db import Conversation
    with Session.begin() as db:
        convo = Conversation(owner_id="owner")
        db.add(convo)
        db.flush()
        cid = convo.id
    store = ConversationGoalStore(Session, cid)
    original = store.load()
    result = UnderstandingResult.model_validate_json(proposal("new_goal", "制定计划"))
    with Session.begin() as db:
        store.save_in_session(db, result, original)
    with __import__("pytest").raises(RuntimeError, match="stale_goal_version"):
        with Session.begin() as db:
            store.save_in_session(db, result, original)
    engine.dispose()
