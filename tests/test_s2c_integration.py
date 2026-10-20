"""Protocol transport, lazy skills and comparison evidence."""
from agent.mcp_stdio_client import StdioMCPClient
from agent.skills import load_instructions
from evals.s2c_benchmark import evaluate


def test_real_stdio_mcp_handshake_discovery_and_call():
    client = StdioMCPClient(timeout_seconds=10)
    result = client.call("mcp_reference_lookup", {"term": "mcp"})
    assert result["ok"] and result["source"] == "trusted_mock_catalog_v1"
    assert client.call("mcp_reference_lookup", {"term": "missing"})["error"] == "reference_not_found"
    assert client.call("mcp_reference_lookup", {"term": "mcp", "extra": 1})["error"] == "invalid_arguments"
    assert client.call("host_shell", {})["error"] == "mcp_tool_not_allowlisted"


def test_progressive_loading_and_budget():
    selected = load_instructions("查询 MCP 参考资料")
    assert "reference:" in selected and "tasks:" not in selected
    assert "tasks:" in load_instructions("任务清单")
    try:
        load_instructions("查询 MCP 参考资料", max_chars=1)
    except ValueError:
        pass
    else:
        raise AssertionError("budget exceeded without rejection")


def test_comparison_harness():
    data = evaluate()
    full, dynamic = data["variants"]["all"], data["variants"]["dynamic"]
    assert full["expected_tool_coverage"] == 1
    assert dynamic["expected_tool_coverage"] == 1
    assert dynamic["mean_tool_count"] < full["mean_tool_count"]
    assert dynamic["mean_schema_chars"] < full["mean_schema_chars"]
