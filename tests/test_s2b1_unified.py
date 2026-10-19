"""S2-B1 vertical acceptance: existing S1 conversation with real Agent decisions."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app

class ContextAwareDecision:
    async def decide(self, messages, tools):
        current = messages[-1]
        if current["role"] == "tool":
            return Decision("计算结果是 42，稍后可以继续引用。", [])
        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        if "7*6" in user_messages[-1]:
            return Decision("", [{"id": "calc", "name": "calculate",
                                  "arguments": {"expression": "7*6"}}])
        if "刚才" in user_messages[-1]:
            assert any("7*6" in x for x in user_messages[:-1])
            assert any(m["role"] == "assistant" and "42" in m["content"]
                       for m in messages)
            return Decision("刚才的结果是 42。", [])
        return Decision("你好，可以直接聊天。", [])

def test_unified_agent_conversation_and_persistence(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path/'u.db'}",
                        model_provider="fake", user_tokens="a:alice,b:bob",
                        allow_demo_identity=False)
    headers={"Authorization": "Bearer a"}
    with TestClient(create_app(settings, agent_decider=ContextAwareDecision())) as client:
        cid=client.post("/conversations",headers=headers).json()["id"]
        url=f"/conversations/{cid}/agent/messages"
        assert client.post(url,json={"content":"你好","client_request_id":"request-0001"}).status_code==401
        assert client.post(url,headers={"Authorization":"Bearer b"},
                           json={"content":"你好","client_request_id":"request-0001"}).status_code==404
        for i,prompt in enumerate(["你好","请计算 7*6","刚才的计算结果是什么？"]):
            response=client.post(url,headers=headers,json={
                "content":prompt,"client_request_id":f"request-{i+1:04d}"})
            assert response.status_code==200, response.text
            assert response.json()["status"]=="completed"
            assert response.json()["conversation_id"]==cid
        history=client.get(f"/conversations/{cid}",headers=headers).json()["messages"]
        assert len(history)==6
        assert [x["role"] for x in history]==["user","assistant"]*3
        assert "42" in history[-1]["content"]
        assert all(x["status"]=="completed" for x in history)
        duplicate=client.post(url,headers=headers,json={
            "content":"你好","client_request_id":"request-0002"})
        assert duplicate.status_code==409

class FailingDecision:
    async def decide(self, messages, tools):
        raise RuntimeError("provider offline")

def test_failed_agent_turn_never_becomes_completed_context(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path/'fail.db'}",
                        model_provider="fake")
    with TestClient(create_app(settings,agent_decider=FailingDecision())) as client:
        cid=client.post("/conversations").json()["id"]
        response=client.post(f"/conversations/{cid}/agent/messages",json={
            "content":"测试失败","client_request_id":"fail-request-001"})
        assert response.status_code==503
        data=client.get(f"/conversations/{cid}").json()["messages"]
        assert data[-1]["status"]=="provider_error"
        assert data[-1]["content"]==""
