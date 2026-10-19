"""Native Tool Calling is exposed only through a persisted conversation."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app
class TwoDecisionToolModel:
    def __init__(self):self.calls=0
    async def decide(self,messages,tools):
        self.calls+=1
        if self.calls==1:
            return Decision("",[{"id":"c1","name":"calculate","arguments":{"expression":"7*6"}}])
        assert any(m["role"]=="tool" and '"value": 42' in m["content"] for m in messages)
        return Decision("The answer is 42.",[])
def test_agent_api_auth_and_trace(tmp_path):
    settings=Settings(database_url=f"sqlite:///{tmp_path/'api.db'}",model_provider="fake",
                      user_tokens="alpha:user-a",allow_demo_identity=False)
    with TestClient(create_app(settings,agent_decider=TwoDecisionToolModel())) as c:
        cid=c.post("/conversations",headers={"Authorization":"Bearer alpha"}).json()["id"]
        path=f"/conversations/{cid}/agent/messages"
        body={"content":"Compute 7*6","client_request_id":"request-123456"}
        assert c.post(path,json=body).status_code==401
        r=c.post(path,headers={"Authorization":"Bearer alpha"},json=body)
        assert r.status_code==200
        result=r.json()
        assert result["status"]=="completed" and result["steps"]==2
        assert result["answer"]=="The answer is 42."
        assert any(t.get("name")=="calculate" for t in result["trace"])
        assert "expression" not in str(result["trace"])
