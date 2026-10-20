"""S2-C skill selection / dispatch and trusted mock MCP via existing Agent API."""
from fastapi.testclient import TestClient
from agent.config import Settings
from agent.decision import Decision
from agent.skills import exposed_tools, select_skills
from agent.tools import TOOL_SCHEMAS
from agent.mock_mcp import MockMCPClient
from apps.api.main import create_app

class MCPDecision:
    def __init__(self, attempted="mcp_reference_lookup"):
        self.attempted=attempted
        self.visible=[]
    async def decide(self, messages, tools):
        self.visible.append([t["function"]["name"] for t in tools])
        if messages[-1]["role"] == "tool":
            return Decision(messages[-1]["content"], [])
        return Decision("",[{"id":"mcp1","name":self.attempted,
                             "arguments":{"term":"mcp"} if self.attempted == "mcp_reference_lookup"
                             else {"title":"should-not-run"}}])

def test_skill_discovery_and_fallback():
    def names(q):
        return [s["function"]["name"] for s in exposed_tools(q, TOOL_SCHEMAS)]
    assert names("请查询 MCP 参考资料") == ["mcp_reference_lookup"]
    assert "create_task" in names("帮我创建待办任务")
    assert len(names("任意模糊请求")) == len(TOOL_SCHEMAS)
    assert len(exposed_tools("查询 MCP",TOOL_SCHEMAS,strategy="all")) == len(TOOL_SCHEMAS)
    assert {s.name for s in select_skills("查询 MCP")} == {"reference"}

def test_mock_mcp_allowlist_and_schema():
    client=MockMCPClient()
    assert client.call("mcp_reference_lookup",{"term":"MCP"})["ok"]
    assert client.call("mcp_reference_lookup",{"term":"unknown"})["error"] == "reference_not_found"
    assert client.call("mcp_reference_lookup",{"term":"MCP","extra":1})["error"] == "invalid_arguments"
    assert client.call("host_shell",{})["error"] == "mcp_tool_not_allowlisted"

def test_single_agent_mcp_execution_and_exposure_fence(tmp_path):
    settings=Settings(database_url=f"sqlite:///{tmp_path/'s2c.db'}",model_provider="fake")
    decision=MCPDecision()
    with TestClient(create_app(settings,agent_decider=decision)) as client:
        cid=client.post("/conversations").json()["id"]
        def ask(text,key):
            return client.post(f"/conversations/{cid}/agent/messages",
                json={"content":text,"client_request_id":key})
        response=ask("查询 MCP 的参考资料","s2c-request-001")
        assert response.status_code == 200, response.text
        assert '"source": "trusted_mock_catalog_v1"' in response.json()["answer"]
        assert decision.visible[0] == ["mcp_reference_lookup"]
        assert any(x["name"]=="mcp_reference_lookup" and x["ok"] for x in response.json()["trace"] if x["type"]=="tool")
    refused=MCPDecision("create_task")
    with TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path/'s2c2.db'}",model_provider="fake"),
                               agent_decider=refused)) as client:
        cid=client.post("/conversations").json()["id"]
        response=client.post(f"/conversations/{cid}/agent/messages",
            json={"content":"查询 MCP 的参考资料","client_request_id":"s2c-request-002"})
        assert response.status_code == 200, response.text
        assert "tool_not_exposed" in response.json()["answer"]
