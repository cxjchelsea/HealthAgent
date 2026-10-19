"""UI smoke: both S1 chat and Agent test mode are discoverable."""
from apps.api.main import create_app
from agent.config import Settings
from fastapi.testclient import TestClient


def test_ui_exposes_chat_and_agent_modes(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path/'web.db'}")
    with TestClient(create_app(settings)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="mode-chat"' in response.text
        assert 'id="mode-agent"' in response.text
        assert "sendAgent" in response.text
        assert "/agent/runs" in response.text
        assert "/conversations/" in response.text
