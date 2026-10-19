"""API integration checks: real LangGraph loop behind authenticated S2-A route."""
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class TwoDecisionToolModel:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{
                "id": "c1", "name": "calculate", "arguments": {"expression": "7*6"}
            }])
        assert any(m["role"] == "tool" and '"value": 42' in m["content"] for m in messages)
        return Decision("The answer is 42.", [])


def test_agent_api_auth_and_trace(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path/'api.db'}", model_provider="fake",
        user_tokens="alpha:user-a", allow_demo_identity=False,
    )
    decider = TwoDecisionToolModel()
    with TestClient(create_app(settings, agent_decider=decider)) as client:
        assert client.post("/agent/runs", json={"content": "Compute 7*6"}).status_code == 401
        response = client.post(
            "/agent/runs", headers={"Authorization": "Bearer alpha"},
            json={"content": "Compute 7*6"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "completed"
        assert result["steps"] == 2
        assert result["answer"] == "The answer is 42."
        assert result["prompt_version"] == "s2a-core-v1"
        assert any(x["type"] == "tool" and x["ok"] for x in result["trace"])
        assert "expression" not in str(result["trace"])
