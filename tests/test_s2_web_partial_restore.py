"""S2 acceptance regression: partial replies remain visible after page reload."""
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class PendingPlanDecider:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "plan", "name": "propose_plan",
                                  "arguments": {"steps": ["Pending manual step"]}}])
        return Decision("该步骤尚未完成。", [])


def test_partial_assistant_reply_survives_conversation_restore(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'partial.db'}",
                        model_provider="fake")
    with TestClient(create_app(settings, agent_decider=PendingPlanDecider())) as client:
        created = client.post("/conversations")
        assert created.status_code == 201
        cid = created.json()["id"]
        sent = client.post(f"/conversations/{cid}/agent/messages",
                           json={"content": "请制定一个两步计划", "client_request_id": "partial-test-001"})
        assert sent.status_code == 200
        assert sent.json()["status"] == "partial"
        messages = client.get(f"/conversations/{cid}").json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["status"] == "partial"
        assert "尚未完成" in messages[-1]["content"]


def test_web_restore_includes_partial_but_excludes_pending():
    html = (Path(__file__).resolve().parents[1] / "apps/web/index.html").read_text(
        encoding="utf-8")
    assert "['completed','partial'].includes(m.status)" in html
    assert "if(m.role==='assistant'&&m.status!=='completed')continue;" not in html
