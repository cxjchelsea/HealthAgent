"""S2-B4 long-term memory: consent, correction, removal, isolation, restart."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app

class MemoryDecider:
    async def decide(self, messages, tools):
        last=messages[-1]
        if last["role"]=="tool":
            return Decision(last["content"],[])
        if last["content"].startswith("SAVE"):
            return Decision("",[{"id":"s","name":"save_memory",
                                 "arguments":{"key":"project","value":"blue whale"}}])
        return Decision("",[{"id":"r","name":"recall_memory",
                             "arguments":{"key":"project"}}])

def test_controlled_memory_lifecycle(tmp_path):
    settings=Settings(database_url=f"sqlite:///{tmp_path/'m.db'}",
                      model_provider="fake",user_tokens="a:alice,b:bob",
                      allow_demo_identity=False)
    ha={"Authorization":"Bearer a"}
    hb={"Authorization":"Bearer b"}
    with TestClient(create_app(settings,agent_decider=MemoryDecider())) as c:
        ca=c.post("/conversations",headers=ha).json()["id"]
        cb=c.post("/conversations",headers=hb).json()["id"]
        def ask(cid,headers,message,nonce,consent=False):
            return c.post(f"/conversations/{cid}/agent/messages",headers=headers,
                json={"content":message,"client_request_id":nonce,
                      "memory_write_consent":consent})
        rejected=ask(ca,ha,"SAVE","memory-00001")
        assert rejected.status_code==200
        assert "memory_write_requires_user_consent" in rejected.json()["answer"]
        assert c.get("/memories",headers=ha).json()==[]
        saved=ask(ca,ha,"SAVE","memory-00002",True)
        assert saved.status_code==200 and '"saved": true' in saved.json()["answer"]
        assert c.get("/memories",headers=ha).json()==[{"key":"project","value":"blue whale"}]
        assert '"found": true' in ask(ca,ha,"RECALL","memory-00003").json()["answer"]
        assert '"found": false' in ask(cb,hb,"RECALL","memory-00005").json()["answer"]
        changed=c.put("/memories/project",headers=ha,json={"value":"green whale"})
        assert changed.status_code==200
        assert "green whale" in ask(ca,ha,"RECALL","memory-00006").json()["answer"]
        assert c.get("/memories",headers=hb).json()==[]
        assert c.delete("/memories/project",headers=ha).status_code==200
        assert '"found": false' in ask(ca,ha,"RECALL","memory-00007").json()["answer"]
    with TestClient(create_app(settings,agent_decider=MemoryDecider())) as c:
        assert c.get("/memories",headers=ha).json()==[]

def test_unauthorized_memory_access(tmp_path):
    settings=Settings(database_url=f"sqlite:///{tmp_path/'auth.db'}",
                      user_tokens="a:alice",allow_demo_identity=False)
    with TestClient(create_app(settings)) as c:
        assert c.get("/memories").status_code==401
        assert c.put("/memories/name",json={"value":"x"}).status_code==401
        assert c.delete("/memories/name").status_code==401
