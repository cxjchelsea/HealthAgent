"""S25-01: integration through the actual HTTP Conversation API (no network model)."""
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app

def proposal(clarify=False):
    return json.dumps({
        "status": "clarification_required" if clarify else "understood",
        "goal": None if clarify else {
            "objective": "整理当前任务", "subgoals": [],
            "constraints": [{"name": "writes", "value": "do not save",
                             "scope": "goal", "source": "explicit"}],
            "success_criteria": []},
        "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
        "clarification": {"required": clarify,
                          "reason_code": "missing_referent" if clarify else "none",
                          "question": "请问指哪项任务？" if clarify else None},
        "schema_version": "s25-01-v1",
    }, ensure_ascii=False)

class CapturingDecider:
    def __init__(self):
        self.messages = None
    async def decide(self, messages, tools):
        self.messages = messages
        return Decision("已根据约束生成草案，未保存。", [])

def test_structured_understanding_passes_validated_goal_to_agent(tmp_path, monkeypatch):
    async def fake_complete(self, *, messages, model):
        assert model == "qwen3:4b"
        return proposal()
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    d = CapturingDecider()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'structured.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=d)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": "把任务整理成草案，不要保存", "client_request_id": "structured-one-001"})
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert d.messages
        assert "Untrusted structured interpretation proposal" in d.messages[-2]["content"]
        assert d.messages[-2]["role"] == "user"
        assert "do not save" in d.messages[-2]["content"]

def test_structured_clarification_stops_before_agent_tools(tmp_path, monkeypatch):
    async def fake_complete(self, *, messages, model):
        return proposal(clarify=True)
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_complete)
    d = CapturingDecider()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'clarify.db'}",
                        model_provider="ollama_proxy", understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=d)) as client:
        cid = client.post("/conversations").json()["id"]
        response = client.post(f"/conversations/{cid}/agent/messages",
            json={"content": "把那个改一下", "client_request_id": "structured-clari-001"})
        assert response.status_code == 200
        assert response.json()["status"] == "partial"
        assert "哪项任务" in response.json()["answer"]
        assert d.messages is None
