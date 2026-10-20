"""Isolated stdio JSON-RPC 2.0 MCP reference server (fixed trusted catalog)."""
import json
import sys

from agent.mock_mcp import MockMCPClient

PROTOCOL = "2025-06-18"
TOOL = {
    "name": "mcp_reference_lookup",
    "description": "Look up a term in the trusted reference catalog.",
    "inputSchema": {
        "type": "object", "properties": {"term": {"type": "string", "minLength": 1, "maxLength": 80}},
        "required": ["term"], "additionalProperties": False,
    },
}


def dispatch(request: dict) -> dict | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    request_id = request.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "healthagent-trusted-reference", "version": "1.0.0"}}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, dict) or params.get("name") != TOOL["name"]:
            result = {"isError": True, "content": [{"type": "text", "text": "mcp_tool_not_allowlisted"}]}
        else:
            response = MockMCPClient().call(TOOL["name"], params.get("arguments", {}))
            result = {"isError": not response["ok"],
                      "content": [{"type": "text", "text": json.dumps(response, ensure_ascii=False)}]}
    else:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main():
    for line in sys.stdin:
        try:
            req = json.loads(line)
            answer = dispatch(req)
        except (ValueError, TypeError):
            answer = {"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": "Parse error"}}
        if answer is not None:
            sys.stdout.write(json.dumps(answer, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
