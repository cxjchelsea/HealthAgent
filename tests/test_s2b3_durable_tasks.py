"""Durable conversation tasks via the single Agent endpoint."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app

class TaskDecider:
    async def decide(self, messages, tools):
        last = messages[-1]
        if last["role"] == "tool":
            return Decision(last["content"], [])
        if last["content"].startswith("CREATE"):
            return Decision("", [{"id":"one","name":"create_task","arguments":{"title":"buy notebook"}}])
        return Decision("", [{"id":"two","name":"list_tasks","arguments":{}}])

def test_persisted_tasks(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path/'tasks.db'}",model_provider="fake",
                        user_tokens="a:alice,b:bob",allow_demo_identity=False)
    headers = {"Authorization":"Bearer a"}
    def ask(client,cid,content,key):
        return client.post(f"/conversations/{cid}/agent/messages",headers=headers,
                           json={"content":content,"client_request_id":key})
    with TestClient(create_app(settings,agent_decider=TaskDecider())) as client:
        cid=client.post("/conversations",headers=headers).json()["id"]
        other=client.post("/conversations",headers=headers).json()["id"]
        r=ask(client,cid,"CREATE","request-0001")
        assert r.status_code==200,r.text
        assert "buy notebook" in r.json()["answer"]
        assert ask(client,cid,"CREATE","request-0001").status_code==409
        assert "buy notebook" in ask(client,cid,"LIST","request-0002").json()["answer"]
        assert '"tasks": []' in ask(client,other,"LIST","request-0003").json()["answer"]
        unauthorized=client.post(f"/conversations/{cid}/agent/messages",
            headers={"Authorization":"Bearer b"},
            json={"content":"LIST","client_request_id":"request-0004"})
        assert unauthorized.status_code==404
    with TestClient(create_app(settings,agent_decider=TaskDecider())) as client:
        assert "buy notebook" in ask(client,cid,"LIST","request-0005").json()["answer"]
