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
        from services.db import database
        from services.goal_store import ConversationGoalStore
        engine, Session = database(settings.database_url)
        assert ConversationGoalStore(Session, cid).load()["goal"] is None
        engine.dispose()


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
        from services.db import database
        from services.goal_store import ConversationGoalStore
        engine, Session = database(settings.database_url)
        assert ConversationGoalStore(Session, cid).load()["goal"] is None
        engine.dispose()


def test_unsaved_draft_can_be_revised_in_same_conversation(tmp_path, monkeypatch):
    """M03->M04: a conversational revision must not require durable goal state."""
    calls = []
    async def fake_understand(self, *, messages, model):
        calls.append(model)
        kind = "new_goal" if len(calls) == 1 else "revise"
        return json.dumps({
            "status": "understood",
            "goal": {"objective": "明日三项文字草案",
                     "subgoals": ["审查材料", "整理项目文档", "总结报告"],
                     "constraints": [{"name": "save", "value": "不要保存",
                                      "source": "explicit", "scope": "goal"}],
                     "success_criteria": []},
            "change": {"kind": kind, "target_goal_id": None, "delta": "第二项修改" if kind == "revise" else None},
            "clarification": {"required": False, "reason_code": "none", "question": None},
            "schema_version": "s25-01-v1"}, ensure_ascii=False)
    async def policy(message, understanding):
        return "respond_only"
    monkeypatch.setattr(HTTPUnderstandingModel, "complete", fake_understand)
    decider = Capture()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'draft_revise.db'}",
                        model_provider="ollama_proxy",
                        understanding_strategy="structured")
    with TestClient(create_app(settings, agent_decider=decider,
                               execution_policy=policy)) as client:
        cid = client.post("/conversations").json()["id"]
        route = f"/conversations/{cid}/agent/messages"
        first = client.post(route, json={"content": "拟三项草案，不保存",
                                         "client_request_id": "draft-first-001"})
        second = client.post(route, json={"content": "第二项改成整理项目文档，其余不变，不保存",
                                          "client_request_id": "draft-revise-002"})
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "completed"
        assert second.json()["execution_mode"] == "respond_only"
        assert decider.tools == []
        from services.db import database
        from services.goal_store import ConversationGoalStore
        engine, Session = database(settings.database_url)
        assert ConversationGoalStore(Session, cid).load()["goal"] is None
        engine.dispose()
