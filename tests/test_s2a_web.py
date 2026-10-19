"""Single Agent UI smoke: no parallel S1/S2 selectors or old API routes."""
from fastapi.testclient import TestClient
from agent.config import Settings
from apps.api.main import create_app
def test_ui_single_agent_mode(tmp_path):
    with TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path/'web.db'}"))) as c:
        text=c.get("/").text
        assert "sendAgent" in text
        assert "/agent/messages" in text
        assert "mode-chat" not in text
        assert "mode-agent" not in text
        assert "/agent/runs" not in text
