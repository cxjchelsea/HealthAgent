"""S25-02 clarification is restored only from a verified latest pending question."""
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class ClarificationDecider:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "ask", "name": "request_clarification",
                                  "arguments": {"question": "你要查哪个词条？"}}])
        if self.calls == 2:
            assert any(m["role"] == "assistant" and m["content"] ==
                       "你要查哪个词条？" for m in messages)
            assert messages[-1]["content"] == "rag"
            return Decision("明白，我来查询 rag。", [])
        if self.calls == 3:
            assert not any(m["role"] == "assistant" and m["content"] ==
                           "你要查哪个词条？" for m in messages)
            return Decision("本轮完成。", [])
        raise AssertionError("unexpected extra turn")


def test_clarification_persists_and_is_consumed_once(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path/'clarify.db'}",
                        model_provider="fake")
    decider = ClarificationDecider()
    with TestClient(create_app(settings, agent_decider=decider)) as client:
        cid = client.post("/conversations").json()["id"]

        def ask(content, request_id):
            return client.post(f"/conversations/{cid}/agent/messages",
                               json={"content": content, "client_request_id": request_id})

        first = ask("查一下参考资料", "clarify-request-001")
        assert first.status_code == 200
        assert first.json()["status"] == "partial"
        assert first.json()["termination_reason"] == "needs_input"

        second = ask("rag", "clarify-request-002")
        assert second.status_code == 200
        assert second.json()["status"] == "completed"

        third = ask("另一个问题", "clarify-request-003")
        assert third.status_code == 200
        assert third.json()["status"] == "completed"
