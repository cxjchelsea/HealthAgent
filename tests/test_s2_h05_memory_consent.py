"""H05: natural-language request does not confer per-turn memory write consent."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class SaveThenFinish:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls % 2:
            return Decision("", [{"id": "save", "name": "save_memory",
                                 "arguments": {"key": "test_preference",
                                               "value": "绿色"}}])
        return Decision("已处理。", [])


def test_h05_requires_per_turn_consent_and_preserves_existing_value(tmp_path):
    url = f"sqlite:///{tmp_path/'h05.db'}"
    model = SaveThenFinish()
    with TestClient(create_app(Settings(database_url=url, model_provider="fake"),
                               agent_decider=model)) as client:
        cid = client.post("/conversations").json()["id"]
        client.put("/memories/test_preference", json={"value": "蓝色"})
        endpoint = f"/conversations/{cid}/agent/messages"
        forbidden = client.post(endpoint, json={
            "content": "请记住偏好现在改成绿色。",
            "client_request_id": "h05-no-consent-001",
            "memory_write_consent": False})
        assert forbidden.status_code == 200
        assert client.get("/memories").json() == [
            {"key": "test_preference", "value": "蓝色"}]
        authorized = client.post(endpoint, json={
            "content": "请记住偏好现在改成绿色。",
            "client_request_id": "h05-with-consent-002",
            "memory_write_consent": True})
        assert authorized.status_code == 200
        assert client.get("/memories").json() == [
            {"key": "test_preference", "value": "绿色"}]
