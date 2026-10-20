"""The official conversation route retrieves owned memories without LLM tool selection."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class NeverCallsTool:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        return Decision("没有你的测试偏好", [])


def test_live_route_reads_owned_memory_even_if_model_skips_tool(tmp_path):
    model = NeverCallsTool()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'recall.db'}",
                        user_tokens="a:alice,b:bob", allow_demo_identity=False,
                        model_provider="fake")
    with TestClient(create_app(settings, agent_decider=model)) as client:
        a = {"Authorization": "Bearer a"}
        b = {"Authorization": "Bearer b"}
        client.put("/memories/测试偏好", headers=a, json={"value": "绿色"})
        client.put("/memories/test_preference", headers=a, json={"value": "蓝色"})
        client.put("/memories/测试偏好", headers=b, json={"value": "红色"})
        cid = client.post("/conversations", headers=a).json()["id"]
        result = client.post(f"/conversations/{cid}/agent/messages", headers=a,
                             json={"content": "你还记得我的测试偏好吗？",
                                   "client_request_id": "memory-read-001"})
        assert result.status_code == 200
        answer = result.json()["answer"]
        assert "测试偏好＝绿色" in answer
        assert "test_preference" in answer
        assert "红色" not in answer
        assert "memory_index_lookup" in [x["type"] for x in result.json()["trace"]]
        assert model.calls == 0
        result = client.post(f"/conversations/{cid}/agent/messages", headers=a,
                             json={"content": "你还记得我之前保存的旅行计划吗？",
                                   "client_request_id": "memory-read-002"})
        assert result.status_code == 200
        assert "当前已保存的 Key" in result.json()["answer"]
        assert "红色" not in result.json()["answer"]


def test_empty_owner_memory_does_not_guess(tmp_path):
    model = NeverCallsTool()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'empty.db'}",
                        model_provider="fake")
    with TestClient(create_app(settings, agent_decider=model)) as client:
        cid = client.post("/conversations").json()["id"]
        result = client.post(f"/conversations/{cid}/agent/messages",
                             json={"content": "你还记得我的测试偏好吗？",
                                   "client_request_id": "memory-empty-001"})
        assert result.status_code == 200
        assert "没有保存的记录" in result.json()["answer"]
        assert model.calls == 0
