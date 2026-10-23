"""Real Conversation HTTP route persists and continues a tool-bound partial plan."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from apps.api.main import create_app


class FirstStepOnly:
    def __init__(self):
        self.calls = 0

    async def decide(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return Decision("", [{"id": "p", "name": "propose_plan",
                                  "arguments": {"steps": [
                                      {"goal": "计算 8*8", "expected_tool": "calculate",
                                       "expected_arguments": {"expression": "8*8"}},
                                      {"goal": "查询 RAG", "expected_tool": "mcp_reference_lookup",
                                       "expected_arguments": {"term": "rag"}},
                                      {"goal": "计算 9*9", "expected_tool": "calculate",
                                       "expected_arguments": {"expression": "9*9"}}]}}])
        if self.calls == 2:
            return Decision("", [{"id": "calc", "name": "calculate",
                                  "arguments": {"expression": "8*8"}}])
        return Decision("没有得到正式计划续接授权，本轮未执行计划步骤。", [])


def test_api_partial_then_resume_original_steps(tmp_path):
    decider = FirstStepOnly()
    settings = Settings(database_url=f"sqlite:///{tmp_path/'resume.db'}",
                        model_provider="fake")
    with TestClient(create_app(settings, agent_decider=decider)) as client:
        cid = client.post("/conversations").json()["id"]
        path = f"/conversations/{cid}/agent/messages"
        first = client.post(path, json={
            "content": "请创建三个步骤的计划：计算8*8、查询RAG、计算9*9。先尝试完成第一步，剩余步骤保留。",
            "client_request_id": "resume-first-001"})
        assert first.status_code == 200
        assert first.json()["status"] == "partial"
        assert [s["status"] for s in first.json()["plan"]] == [
            "completed", "pending", "pending"]
        second = client.post(path, json={
            "content": "请继续完成刚才计划中尚未完成的步骤，并给出最终结果。",
            "client_request_id": "resume-second-002"})
        assert second.status_code == 200
        result = second.json()
        # Legacy direct mode must not auto-execute saved steps on keywords.
    assert not any(t.get("type") == "plan_resume_execution" for t in result["trace"])
    assert not any(t.get("type") == "tool" for t in result["trace"])
    assert [s["status"] for s in result["plan"]] == [
        "completed", "pending", "pending"]
