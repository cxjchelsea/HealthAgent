"""End-to-end host tool restriction with an independently injected policy."""
import json
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from agent.understanding import HTTPUnderstandingModel
from apps.api.main import create_app


class Capture:
    def __init__(self):
        self.tools = None
    async def decide(self, messages, tools):
        self.tools = tools
        return Decision(content="已生成不保存的文字草案。", tool_calls=[])


def test_conversation_response_only_policy_excludes_all_tools(tmp_path, monkeypatch):
    async def fake_understand(self, *, messages, model):
        return json.dumps({
            "status": "understood",
            "goal": {"objective": "生成明日任务草案", "subgoals": [],
                     "constraints": [{"name": "save", "value": "不要保存",
                                      "source": "explicit", "scope": "goal"}],
                     "success_criteria": []},
            "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None},
            "schema_version": "s25-01-v1",
        }, ensure_ascii=False)

    async def policy(message, understanding):
        assert understanding["goal"]["objective"] == "生成明日任务草案"
        return "respond_only"

    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_understand)
    decider = Capture()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'policy.db'}",
                        model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=decider,
                               execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "请给明天拟三项草案，不要保存",
            "client_request_id": "policy-draft-001"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["status"] == "completed"
        assert decider.tools == []


def test_conversation_read_only_policy_blocks_mutations(tmp_path, monkeypatch):
    async def fake_understand(self, *, messages, model):
        return json.dumps({
            "status": "understood",
            "goal": {"objective": "查询任务", "subgoals": [],
                     "constraints": [], "success_criteria": []},
            "change": {"kind": "new_goal", "target_goal_id": None, "delta": None},
            "clarification": {"required": False, "reason_code": "none", "question": None},
            "schema_version": "s25-01-v1"})
    async def policy(message, understanding):
        return "read_only"
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_understand)
    decider = Capture()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'readonly.db'}",
                        model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=decider,
                               execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        reply = client.post(f"/conversations/{cid}/agent/messages", json={
            "content": "查询当前任务",
            "client_request_id": "policy-lookup-001"})
        assert reply.status_code == 200, reply.text
        names = {item["function"]["name"] for item in decider.tools}
        assert "list_tasks" in names
        assert "create_task" not in names
        assert "save_memory" not in names
        assert "propose_plan" not in names
