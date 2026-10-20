"""Bounded MCP stdio client with initialize, tool discovery, and tools/call.

Only starts the checked-in trusted server module, never a model-provided command.
A separate subprocess isolates the mock service from the Agent interpreter.
"""
import json
import subprocess
import sys


class StdioMCPClient:
    def __init__(self, timeout_seconds: float = 5.0):
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("MCP timeout must be in (0, 30]")
        self.timeout_seconds = timeout_seconds

    def call(self, name: str, arguments: dict) -> dict:
        if name != "mcp_reference_lookup":
            return {"ok": False, "error": "mcp_tool_not_allowlisted"}
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "healthagent", "version": "1.0.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": name, "arguments": arguments}},
        ]
        data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
        try:
            finished = subprocess.run(
                [sys.executable, "-m", "agent.mcp_stdio_server"],
                input=data, text=True, capture_output=True, timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "mcp_timeout"}
        except OSError:
            return {"ok": False, "error": "mcp_transport_error"}
        if finished.returncode or len(finished.stdout) > 100000:
            return {"ok": False, "error": "mcp_transport_error"}
        try:
            frames = [json.loads(s) for s in finished.stdout.splitlines()]
            indexed = {frame["id"]: frame for frame in frames}
            assert set(indexed) == {1, 2, 3}
            assert indexed[1]["result"]["protocolVersion"] == "2025-06-18"
            available = indexed[2]["result"]["tools"]
            assert any(t["name"] == name for t in available)
            call = indexed[3]["result"]
            assert len(call["content"]) == 1 and call["content"][0]["type"] == "text"
            response = json.loads(call["content"][0]["text"])
            assert isinstance(response, dict) and isinstance(response.get("ok"), bool)
            assert call.get("isError", False) == (not response["ok"])
            return response
        except (AssertionError, KeyError, TypeError, ValueError):
            return {"ok": False, "error": "mcp_protocol_error"}
