"""Unified Agent failure and result persistence, replacing removed SSE checks."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app

class Failure:
    async def decide(self, messages, tools):
        raise RuntimeError("private upstream endpoint")

class Normal:
    async def decide(self, messages, tools):
        return Decision("three pieces in one completed answer", [])

def _run(tmp_path,decider):
    settings=Settings(database_url=f"sqlite:///{tmp_path/'run.db'}",model_provider="fake")
    with TestClient(create_app(settings,agent_decider=decider)) as c:
        cid=c.post("/conversations").json()["id"]
        result=c.post(f"/conversations/{cid}/agent/messages",json={
            "content":"test","client_request_id":"req-12345678"})
        history=c.get(f"/conversations/{cid}").json()["messages"]
    return result,history

def test_provider_failure_not_completed(tmp_path):
    result,history=_run(tmp_path,Failure())
    assert result.status_code==503
    assert "private upstream endpoint" not in result.text
    assert history[-1]["status"]=="provider_error"
    assert history[-1]["content"]==""

def test_successful_agent_result_persisted(tmp_path):
    result,history=_run(tmp_path,Normal())
    assert result.status_code==200
    assert result.json()["status"]=="completed"
    assert history[-1]["status"]=="completed"
    assert history[-1]["content"]=="three pieces in one completed answer"
